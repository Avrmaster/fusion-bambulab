"""Bring a Bambu project in line with its build configs WITHOUT throwing away what was done by hand in Bambu Studio.

    sync(proj, plate_list, project=..., filament=1, title='', rebuild=False, accept_loss=False, dry=False) -> report

plate_list / project / filament / title are what bambu3mf.build() takes. A packed plate may carry lay['_group'] (the label shared by
all plates of one packed group): parts of a group can move between its plates.

What happens:
  1. Same plates and the same parts on them (by name; Bambu's own " (2)" copies count as the same name): nothing is rebuilt.
     - a part whose STL / orientation changed gets the new mesh in place: position, turn, plate, settings stay (bbox centre and
       bed contact kept, like update_3mf.py); its support blockers are re-made with it
     - per-object settings, the filament and the project-wide overrides are merged three ways — the last build (the snapshot
       <proj>.sync.json), the configs now, the project as Bambu saved it: a config change is applied where the value was not
       touched in Bambu, a value changed in Bambu is kept (and listed, to fold into the config), both changed = a conflict
     - plates with a new mesh are checked: inside the bed, and no part closer to a neighbour than the brims need (or than it
       already was). Only a plate that fails is laid out again (packed groups: the whole group, from the new packing)
  2. Parts or plates added / removed (or rebuild=True): the project is rebuilt from the configs, then every manual setting and
     every manual position on a plate that did not change is put back. Whatever would still be lost — conflicts, positions on
     changed plates — is listed and nothing is written unless accept_loss=True.
Writes nothing while Bambu Studio is running (saving there would overwrite the file)."""
import os, re, json, math, sys, subprocess, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bambu3mf

MS, MODEL = 'Metadata/model_settings.config', '3D/3dmodel.model'
COPY = re.compile(r' \(\d+\)$')
base_name = lambda n: COPY.sub('', n)


# ---------------- reading a project ----------------
def read(proj):
    infos, files = bambu3mf.load(proj)
    cfg, model = files[MS].decode(), files[MODEL].decode()
    P = {'infos': infos, 'files': files, 'objects': {}, 'plates': []}
    items = {oid: [float(x) for x in t.split()] for oid, t in re.findall(r'<item objectid="(\d+)"[^>]*?transform="([^"]+)"', model)}
    comps = {}
    for oid, body in re.findall(r'<object id="(\d+)"[^>]*>\s*<components>(.*?)</components>', model, re.S):
        comps[oid] = {mid: path.lstrip('/') for path, mid in re.findall(r'<component p:path="([^"]+)" objectid="(\d+)"', body)}
    for oid, body in re.findall(r'<object id="(\d+)">(.*?)</object>', cfg, re.S):
        head = body.split('<part')[0]
        meta = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', head))
        name = meta.pop('name'); ext = meta.pop('extruder', '1')
        parts = re.findall(r'<part id="(\d+)" subtype="([^"]+)"', body)
        P['objects'][oid] = {'name': name, 'base': base_name(name), 'settings': meta, 'extruder': ext, 'parts': parts,
                             'comps': comps.get(oid, {}), 'transform': items.get(oid)}
    for pl in re.findall(r'<plate>(.*?)</plate>', cfg, re.S):
        P['plates'].append({'name': re.search(r'plater_name" value="([^"]*)"', pl).group(1),
                            'oids': re.findall(r'object_id" value="(\d+)"', pl)})
    P['ps'] = json.loads(files['Metadata/project_settings.config'])
    return P


def comp_offset(P, oid, pid):
    """The translation of part pid's component inside object oid. Bambu Studio re-centres every part mesh on save and
    moves the offset into this transform (support blockers came back shifted by it); rotations are not used."""
    body = re.search(r'<object id="%s"[^>]*>(.*?)</object>' % oid, P['files'][MODEL].decode(), re.S)
    m = body and re.search(r'<component [^>]*objectid="%s"[^>]*?transform="([^"]+)"' % pid, body.group(1))
    if not m: return (0.0, 0.0, 0.0)
    t = [float(x) for x in m.group(1).split()]
    return tuple(t[9:12])


def part_mesh(P, oid, pid):
    """Part mesh in the object's frame (its component offset applied)."""
    o = P['objects'][oid]; path = o['comps'][pid]; om = P['files'][path].decode()
    blk = re.search(r'<object id="%s"[^>]*>\s*<mesh>(.*?)</mesh>' % pid, om, re.S).group(1)
    d = comp_offset(P, oid, pid)
    V = [(float(a) + d[0], float(b) + d[1], float(c) + d[2]) for a, b, c in re.findall(r'<vertex x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"', blk)]
    F = [tuple(int(x) for x in t) for t in re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', blk)]
    return V, F


def signature(V, F):
    lo, hi = bambu3mf.bbox(V)
    return [len(F), round(bambu3mf.volume(V, F), 3)] + [round(hi[i] - lo[i], 4) for i in range(3)]


def same_mesh(a, b):
    """Same triangle count, volume within 0.05 mm³ (absolute; the file's float precision moves it by ~0.01), bbox within 0.005 mm."""
    return a[0] == b[0] and abs(a[1] - b[1]) < 0.05 and all(abs(x - y) < 0.005 for x, y in zip(a[2:], b[2:]))


def main_part(o):
    return [pid for pid, st in o['parts'] if st == 'normal_part']


def blocker_parts(o):
    return [pid for pid, st in o['parts'] if st == 'support_blocker']


def same_blockers(P, oid, o, V0, V, bl, tol=0.01):
    """The project's blocker boxes sit where the configs put them (both taken relative to their part mesh's bbox corner)."""
    lo0, lo = bambu3mf.bbox(V0)[0], bambu3mf.bbox(V)[0]
    for pid, (BV, BF) in zip(blocker_parts(o), bl):
        a, b = bambu3mf.bbox(part_mesh(P, oid, pid)[0]), bambu3mf.bbox(BV)
        if any(abs((a[k][i] - lo0[i]) - (b[k][i] - lo[i])) > tol for k in (0, 1) for i in range(3)): return False
    return True


# ---------------- the configs as data ----------------
def desired(plate_list, settings=None, extra=None, filament=1):
    extra = {bambu3mf.bambu_name(k): v for k, v in (extra or {}).items()}
    D = {'plates': [], 'objects': {}}
    seen = {}
    for pname, objs, *lay in plate_list:
        lay = lay[0] if lay else {}; pname = bambu3mf.bambu_name(pname); group = lay.get('_group', pname)
        keys = []
        for k, o in enumerate(objs):
            name = bambu3mf.bambu_name(o[0]); per = dict(o[3]) if len(o) > 3 else {}
            bn = base_name(name)                             # copies are 'X (2)' in the configs too, as in Bambu
            idx = seen.setdefault((group, bn), 0); seen[(group, bn)] = idx + 1
            key = '%s|%s|%d' % (group, bn, idx)
            sets = {k2: str(v) for k2, v in {**(settings or {}), **extra.get(name, {}), **per}.items() if not k2.startswith('_')}
            D['objects'][key] = {'name': name, 'base': bn, 'obj': (name,) + tuple(o[1:]), 'settings': sets, 'extruder': str(per.get('_filament', filament)),
                                 'blockers': len(per.get('_blockers', [])), 'plate': pname, 'group': group, 'slot': k, 'lay': lay}
            keys.append(key)
        D['plates'].append({'name': pname, 'group': group, 'keys': keys, 'lay': lay, 'objs': objs})
    return D


def packed_angle(d):
    return d['lay']['_placed'][d['slot']][2] if '_placed' in d['lay'] else 0.0


def posed(d, stl_dir, angle=None):
    """The mesh build() would write for this object (bed face down, packed turn applied), centred; and its blocker boxes.
    angle: the packed turn to use (default: the one of the current packing)."""
    V, F, size, bl = bambu3mf.prep_object(d['obj'], stl_dir)
    a = packed_angle(d) if angle is None else angle
    if a:
        a = math.radians(a); ca, sa = math.cos(a), math.sin(a)
        rz = lambda P: [(ca * x - sa * y, sa * x + ca * y, z) for x, y, z in P]
        V = rz(V); bl = [(rz(BV), BF) for BV, BF in bl]
    return V, F, bl


def match(P, D):
    """key -> current object id, or None when the plates / parts differ (a structural change)."""
    groups = {p['name']: p['group'] for p in D['plates']}
    if sorted(p['name'] for p in P['plates']) != sorted(groups):
        return None, 'plates differ: in the project only %s; in the configs only %s' % (
            sorted(set(p['name'] for p in P['plates']) - set(groups)), sorted(set(groups) - set(p['name'] for p in P['plates'])))
    cur = {}
    for pl in P['plates']:
        for oid in pl['oids']:
            o = P['objects'][oid]; g = groups[pl['name']]
            cur.setdefault((g, o['base']), []).append(oid)
    want = {}
    for key, d in D['objects'].items():
        want.setdefault((d['group'], d['base']), []).append(key)
    if {k: len(v) for k, v in cur.items()} != {k: len(v) for k, v in want.items()}:
        a = {k: len(v) for k, v in cur.items()}; b = {k: len(v) for k, v in want.items()}
        diff = sorted('%s: %d in the project, %d in the configs' % (k[1], a.get(k, 0), b.get(k, 0)) for k in set(a) | set(b) if a.get(k, 0) != b.get(k, 0))
        return None, 'parts differ: ' + '; '.join(diff)
    M = {}
    for gk, keys in want.items():
        for key, oid in zip(sorted(keys, key=lambda s: int(s.rsplit('|', 1)[1])), cur[gk]):
            M[key] = oid
    for key, oid in M.items():
        o = P['objects'][oid]
        if len(main_part(o)) != 1:
            return None, '%s: %d parts in Bambu (split / merged by hand?)' % (o['name'], len(main_part(o)))
    return M, ''


# ---------------- three-way merge ----------------
def merge3(base, ours, theirs):
    """dicts of str -> str (missing = None). Returns final, applied {k: (old, new)}, kept {k: (config, bambu)}, conflicts {k: (base, config, bambu)}."""
    final, applied, kept, conflicts = {}, {}, {}, {}
    for k in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(k), ours.get(k), theirs.get(k)
        if o == t: v = t
        elif t == b: v = o; applied[k] = (t, o)
        elif o == b: v = t; kept[k] = (o, t)
        else: v = t; conflicts[k] = (b, o, t)
        if v is not None: final[k] = v
    return final, applied, kept, conflicts


def merge4(base_cfg, base_built, ours, theirs):
    """merge3 with two bases: the configs at the last sync (base_cfg) and the project as built then (base_built). A value that
    was kept from Bambu at the last sync differs between the two; comparing the config with base_cfg and Bambu with base_built
    keeps it kept, instead of reading the unchanged config as a new config change."""
    final, applied, kept, conflicts = {}, {}, {}, {}
    for k in sorted(set(base_cfg) | set(base_built) | set(ours) | set(theirs)):
        bc, bb_, o, t = base_cfg.get(k), base_built.get(k), ours.get(k), theirs.get(k)
        if o == t: v = t
        elif o == bc: v = t; kept[k] = (o, t)
        elif t == bb_: v = o; applied[k] = (t, o)
        else: v = t; conflicts[k] = (bc, o, t)
        if v is not None: final[k] = v
    return final, applied, kept, conflicts


def project_values(ps, keys):
    return {k: json.dumps(ps[k]) if isinstance(ps.get(k), list) else ps.get(k) for k in keys if k in ps}


def project_base(S, ps, project):
    """The project values at the last sync. A key new in the config has none: its base is Bambu's value when Bambu still has its
    system default there (not in different_settings_to_system), so the new override applies instead of reading as a conflict."""
    base = dict((S or {}).get('project') or project_values(dict(ps, **project), project))
    edited = set(((ps.get('different_settings_to_system') or [''])[0]).split(';'))
    for k, v in project_values(ps, [k for k in project if k not in base and k not in edited]).items(): base[k] = v
    return base


# ---------------- writing ----------------
def set_object_meta(cfg, oid, settings, extruder):
    body = re.search(r'<object id="%s">(.*?)</object>' % oid, cfg, re.S).group(1)
    head = body.split('<part')[0]
    name = re.search(r'<metadata key="name" value="([^"]*)"/>', head).group(1)
    fc = re.search(r'\s*<metadata face_count="\d+"/>', head)
    nh = '\n    <metadata key="name" value="%s"/>\n' % name + ''.join('    <metadata key="%s" value="%s"/>\n' % kv for kv in settings.items()) \
         + '    <metadata key="extruder" value="%s"/>' % extruder + (fc.group(0) if fc else '') + '\n    '
    return cfg.replace('<object id="%s">' % oid + body, '<object id="%s">' % oid + nh + body[len(head):], 1)


def set_mesh(P, oid, pid, V, F):
    """Write part pid's mesh so that it lands at V in the object's frame (its component offset subtracted)."""
    d = comp_offset(P, oid, pid)
    if any(d): V = [(x - d[0], y - d[1], z - d[2]) for x, y, z in V]
    o = P['objects'][oid]; path = o['comps'][pid]; om = P['files'][path].decode()
    blk = re.search(r'(<object id="%s"[^>]*>\s*<mesh>)(.*?)(</mesh>)' % pid, om, re.S)
    P['files'][path] = (om[:blk.start(2)] + bambu3mf.mesh_xml(V, F) + om[blk.end(2):]).encode()


def set_face_counts(cfg, oid, total, main_pid, main_faces):
    body = re.search(r'<object id="%s">(.*?)</object>' % oid, cfg, re.S).group(1)
    nb = re.sub(r'<metadata face_count="\d+"/>', '<metadata face_count="%d"/>' % total, body, count=1)
    nb = re.sub(r'(<part id="%s" subtype="normal_part".*?<mesh_stat face_count=")\d+' % main_pid, r'\g<1>%d' % main_faces, nb, count=1, flags=re.S)
    return cfg.replace(body, nb, 1)


def tr_str(t):
    return ' '.join('%.9g' % x for x in t)


def set_transform(P, cfg, oid, t):
    m = P['files'][MODEL].decode()
    m = re.sub(r'(<item objectid="%s"[^>]*?transform=")[^"]+' % oid, lambda g: g.group(1) + tr_str(t), m, 1)
    P['files'][MODEL] = m.encode(); P['objects'][oid]['transform'] = t
    return re.sub(r'(<assemble_item object_id="%s"[^>]*?transform=")[^"]+' % oid, lambda g: g.group(1) + tr_str(t), cfg, 1)


def apply_tr(t, v):
    """3MF transform (12 numbers: 3x3 row-major then translation, row vectors) applied to a point."""
    x, y, z = v
    return (x * t[0] + y * t[3] + z * t[6] + t[9], x * t[1] + y * t[4] + z * t[7] + t[10], x * t[2] + y * t[5] + z * t[8] + t[11])


def plate_origin(i, n, bed=256.0):
    cols = math.ceil(math.sqrt(n) - 1e-9); s = bed * 1.2
    return (i % cols) * s, -(i // cols) * s


GUARD = True   # tests on copies switch it off


def bambu_running():
    if not GUARD: return False
    try:
        if os.name == 'nt':
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq bambu-studio.exe'], capture_output=True, text=True).stdout
            return 'bambu-studio.exe' in out.lower()
        return subprocess.run(['pgrep', '-x', 'BambuStudio'], capture_output=True).returncode == 0
    except Exception:
        return False


# ---------------- the fit check ----------------
class Rect:
    """The bounding-rectangle stand-in when shapely is missing: distances read shorter than the real outlines,
    so a crowded plate may be laid out again when it did not need it, never the other way round."""
    def __init__(self, x0, y0, x1, y1): self.b = (x0, y0, x1, y1)
    def contains(self, o): return self.b[0] <= o.b[0] and self.b[1] <= o.b[1] and o.b[2] <= self.b[2] and o.b[3] <= self.b[3]
    def distance(self, o): return math.hypot(max(o.b[0] - self.b[2], self.b[0] - o.b[2], 0), max(o.b[1] - self.b[3], self.b[1] - o.b[3], 0))


def footprint(V, F, t):
    W = [apply_tr(t, v) for v in V]; z = min(q[2] for q in W)
    try: from shapely.geometry import MultiPoint
    except ImportError: return Rect(min(q[0] for q in W), min(q[1] for q in W), max(q[0] for q in W), max(q[1] for q in W)), z
    return MultiPoint([(x, y) for x, y, _ in W]).convex_hull, z


def brim_of(o, ps):
    b = o['settings'].get('brim_width', ps.get('brim_width', '5'))
    return float(b[0] if isinstance(b, list) else b)


def check_plate(P, pi, changed, before, bed=256.0):
    """Problems on plate pi after new meshes went in. before: {oid: footprint before the swap}."""
    pl = P['plates'][pi]; ox, oy = plate_origin(pi, len(P['plates']), bed)
    try: from shapely.geometry import box
    except ImportError: box = Rect
    bedbox = box(ox + 2, oy + 2, ox + bed - 2, oy + bed - 2)
    fps = {}
    for oid in pl['oids']:
        o = P['objects'][oid]; V, F = part_mesh(P, oid, main_part(o)[0]); fps[oid] = footprint(V, F, o['transform'])
    probs = []
    for oid in pl['oids']:
        if oid not in changed: continue
        fp, zmin = fps[oid]; name = P['objects'][oid]['name']
        if not bedbox.contains(fp) and (oid not in before or bedbox.contains(before[oid])):
            probs.append('%s sticks out of the bed' % name)
        for other in pl['oids']:
            if other == oid: continue
            need = 2 * max(brim_of(P['objects'][oid], P['ps']), brim_of(P['objects'][other], P['ps'])) + 1
            d = fp.distance(fps[other][0]); was = before[oid].distance(fps[other][0]) if oid in before else need
            if d < min(need, was) - 1e-3:
                probs.append('%s is %.1f mm from %s (needs %.1f)' % (name, d, P['objects'][other]['name'], min(need, was)))
    return probs


# ---------------- snapshot ----------------
def snap_path(proj):
    return os.path.splitext(proj)[0] + '.sync.json'


def take_snapshot(P, D, M, project_keys):
    S = {'version': 1, 'project': project_values(P['ps'], project_keys), 'objects': {}}
    for key, oid in M.items():
        o = P['objects'][oid]; V, F = part_mesh(P, oid, main_part(o)[0])
        d = D['objects'][key]
        S['objects'][key] = {'settings': o['settings'], 'extruder': o['extruder'], 'cfg_settings': d['settings'], 'cfg_extruder': d['extruder'], 'mesh': d.get('_sig') or signature(V, F),
                             'angle': d.get('_angle', packed_angle(d)),
                             'transform': o['transform'], 'plate': [p['name'] for p in P['plates'] if oid in p['oids']][0]}
    return S


# ---------------- the main entry ----------------
def sync(proj, plate_list, stl_dir='/', settings=None, extra=None, filament=1, title='', bed=None, project=None,
         rebuild=False, accept_loss=False, dry=False):
    project = project or {}
    D = desired(plate_list, settings, extra, filament)
    P = read(proj)
    bed = bed or bambu3mf.bed_size(json.loads(P['files']['Metadata/project_settings.config']))
    S = json.load(open(snap_path(proj), encoding='utf-8')) if os.path.exists(snap_path(proj)) else None
    rep = {'swapped': [], 'applied': [], 'kept': [], 'conflicts': [], 'relaid': [], 'lost': [], 'notes': []}
    if S is None:
        rep['notes'].append('no snapshot of the last build yet: taking the configs as what was built last')
    M, why = match(P, D)
    if rebuild or M is None:
        return _rebuild(proj, plate_list, stl_dir, settings, extra, filament, title, bed, project, P, D, S, rep,
                        why or 'rebuild asked for', accept_loss, dry)
    # --- settings, filament, project overrides: three ways
    cfg = P['files'][MS].decode()
    for key, oid in M.items():
        d, o = D['objects'][key], P['objects'][oid]
        sb = (S or {}).get('objects', {}).get(key)
        base_s = sb['settings'] if sb else d['settings']; base_e = sb['extruder'] if sb else d['extruder']
        cfg_s = sb.get('cfg_settings', base_s) if sb else base_s; cfg_e = sb.get('cfg_extruder', base_e) if sb else base_e
        fin, ap, kp, cf = merge4(cfg_s, base_s, d['settings'], o['settings'])
        fe, ape, kpe, cfe = merge4({'filament': cfg_e}, {'filament': base_e}, {'filament': d['extruder']}, {'filament': o['extruder']})
        for k, v in {**ap, **ape}.items(): rep['applied'].append('%s: %s %s -> %s' % (o['name'], k, v[0], v[1]))
        for k, v in {**kp, **kpe}.items(): rep['kept'].append('%s: %s = %s in Bambu (config: %s)' % (o['name'], k, v[1], 'not set' if v[0] is None else v[0]))
        for k, v in {**cf, **cfe}.items(): rep['conflicts'].append('%s: %s was %s, config now %s, Bambu %s' % (o['name'], k, *v))
        if fin != o['settings'] or fe['filament'] != o['extruder']:
            cfg = set_object_meta(cfg, oid, fin, fe['filament']); o['settings'] = fin; o['extruder'] = fe['filament']
    pbase = project_base(S, P['ps'], project)
    pours = project_values(dict(P['ps'], **project), project)
    ptheirs = project_values(P['ps'], project)
    pf, pap, pkp, pcf = merge3(pbase, pours, ptheirs)
    for k, v in pap.items(): rep['applied'].append('project: %s -> %s' % (k, str(v[1])[:60]))
    for k, v in pkp.items(): rep['kept'].append('project: %s changed in Bambu (kept)' % k)
    for k, v in pcf.items(): rep['conflicts'].append('project: %s changed both in the config and in Bambu' % k)
    if rep['conflicts'] and not accept_loss:
        rep['refused'] = 'conflicts: settings changed both in the configs and in Bambu'
        return rep
    if pap:
        bambu3mf.set_project(P['ps'], {k: project[k] for k in pap})
        P['files']['Metadata/project_settings.config'] = json.dumps(P['ps'], indent=4).encode()
    # --- meshes
    changed, before = {}, {}
    for key, oid in M.items():
        d, o = D['objects'][key], P['objects'][oid]
        sb = (S or {}).get('objects', {}).get(key)
        ang = sb.get('angle', packed_angle(d)) if sb else packed_angle(d); d['_angle'] = ang   # the turn it has in the project
        V, F, bl = posed(d, stl_dir, ang); sig = signature(V, F); d['_sig'] = sig
        mid = main_part(o)[0]; V0, F0 = part_mesh(P, oid, mid)
        last = sb['mesh'] if sb else signature(V0, F0)
        if same_mesh(sig, last) and len(bl) == len(blocker_parts(o)) and same_blockers(P, oid, o, V0, V, bl): continue
        if len(bl) != len(blocker_parts(o)):
            return _rebuild(proj, plate_list, stl_dir, settings, extra, filament, title, bed, project, P, D, S, rep,
                            '%s: support blockers %d -> %d' % (o['name'], len(blocker_parts(o)), len(bl)), accept_loss, dry)
        before[oid] = footprint(V0, F0, o['transform'])[0]
        lo0, hi0 = bambu3mf.bbox(V0); lo, hi = bambu3mf.bbox(V)
        off = [(lo0[0] + hi0[0]) / 2 - (lo[0] + hi[0]) / 2, (lo0[1] + hi0[1]) / 2 - (lo[1] + hi[1]) / 2, lo0[2] - lo[2]]
        mv = lambda Q: [(x + off[0], y + off[1], z + off[2]) for x, y, z in Q]
        set_mesh(P, oid, mid, mv(V), F)
        for pid, (BV, BF) in zip(blocker_parts(o), bl): set_mesh(P, oid, pid, mv(BV), BF)
        cfg = set_face_counts(cfg, oid, len(F) + 12 * len(bl), mid, len(F))
        changed[oid] = key; rep['swapped'].append(o['name'])
    # --- fit check on the plates that got a new mesh; lay out again only what fails
    redo_groups = set()
    for pi, pl in enumerate(P['plates']):
        if not any(oid in changed for oid in pl['oids']): continue
        probs = check_plate(P, pi, changed, before, bed)
        if probs:
            g = [p['group'] for p in D['plates'] if p['name'] == pl['name']][0]
            redo_groups.add(g); rep['relaid'].append('%s: %s' % (pl['name'], '; '.join(probs)))
    for g in redo_groups:
        cfg = _relayout(P, D, M, cfg, g, stl_dir, bed, rep)
    rep['changed'] = bool(changed or redo_groups or pap or any(True for _ in rep['applied']))
    if dry or not rep['changed']:
        if not dry and S is None: _write_snapshot(proj, P, D, M, project)
        return rep
    if bambu_running():
        rep['refused'] = 'Bambu Studio is running: close it (saving there would overwrite the file), then run this again'
        return rep
    P['files'][MS] = cfg.encode()
    bambu3mf.save(proj, P['files'], P['infos'], zipfile.ZIP_STORED)
    _write_snapshot(proj, read(proj), D, M, project)
    return rep


def _write_snapshot(proj, P, D, M, project):
    json.dump(take_snapshot(P, D, M, project), open(snap_path(proj), 'w', encoding='utf-8'), indent=1, ensure_ascii=False)


def _relayout(P, D, M, cfg, group, stl_dir, bed, rep):
    """Lay the plates of one group out as build() would (new meshes, configured layout / packing); settings stay."""
    names = [p['name'] for p in D['plates'] if p['group'] == group]
    n = len(P['plates'])
    for dp in [p for p in D['plates'] if p['group'] == group]:
        pi = [i for i, p in enumerate(P['plates']) if p['name'] == dp['name']][0]; ox, oy = plate_origin(pi, n, bed)
        meshes = []
        for key in dp['keys']:
            d = D['objects'][key]; V, F, bl = posed(d, stl_dir); d['_angle'] = packed_angle(d); d['_sig'] = signature(V, F)
            lo, hi = bambu3mf.bbox(V); meshes.append((V, F, bl, [hi[i] - lo[i] for i in range(3)]))
        pos = bambu3mf.plate_positions([m[3] for m in meshes], dp['lay'], bed)
        for k, key in enumerate(dp['keys']):
            oid = M[key]; o = P['objects'][oid]; V, F, bl, size = meshes[k]
            mid = main_part(o)[0]; set_mesh(P, oid, mid, V, F)
            for pid, (BV, BF) in zip(blocker_parts(o), bl): set_mesh(P, oid, pid, BV, BF)
            cfg = set_face_counts(cfg, oid, len(F) + 12 * len(bl), mid, len(F))
            cfg = set_transform(P, cfg, oid, [1, 0, 0, 0, 1, 0, 0, 0, 1, ox + pos[k][0], oy + pos[k][1], size[2] / 2])
        # objects of a packed group may change plates
        want = [M[key] for key in dp['keys']]
        P['plates'][pi]['oids'] = want
    # rewrite the model_instance lists of the group's plates
    inst = {oid: blk for blk, oid in re.findall(r'(\s*<model_instance>\s*<metadata key="object_id" value="(\d+)"/>.*?</model_instance>)', cfg, re.S)}
    want = {p['name']: p['oids'] for p in P['plates'] if p['name'] in names}
    def plate(mo):
        blk = mo.group(0); nm = re.search(r'plater_name" value="([^"]*)"', blk).group(1)
        if nm not in want: return blk
        blk = re.sub(r'\s*<model_instance>.*?</model_instance>', '', blk, flags=re.S)
        return blk.replace('</plate>', ''.join(inst[o] for o in want[nm]) + '\n  </plate>')
    cfg = re.sub(r'<plate>.*?</plate>', plate, cfg, flags=re.S)
    return cfg


def _rebuild(proj, plate_list, stl_dir, settings, extra, filament, title, bed, project, P, D, S, rep, why, accept_loss, dry):
    """Rebuild from the configs, then put back every manual setting and every manual position on plates that did not change."""
    rep['rebuild'] = why
    old = P
    # what was done by hand: settings that differ from the last build, positions that moved since it
    groups = {p['name']: p['group'] for p in D['plates']}
    old_by_key = {}
    seen = {}
    for pl in old['plates']:
        g = groups.get(pl['name'], pl['name'])
        for oid in pl['oids']:
            o = old['objects'][oid]; i = seen.setdefault((g, o['base']), 0); seen[(g, o['base'])] = i + 1
            old_by_key['%s|%s|%d' % (g, o['base'], i)] = (oid, pl['name'])
    # a part whose plate was renamed (a part added to / removed from it) is still the same part: match it by its name
    # alone when that name is unique on both sides
    def by_part(keys):
        out = {}
        for k in keys: out.setdefault(k.split('|', 1)[1], []).append(k)
        return {sfx: ks[0] for sfx, ks in out.items() if len(ks) == 1}
    new_by_part, old_by_part = by_part(D['objects']), by_part(old_by_key)
    for k in list(old_by_key):
        sfx = k.split('|', 1)[1]
        if k not in D['objects'] and sfx in new_by_part and old_by_part.get(sfx) == k:
            old_by_key[new_by_part[sfx]] = old_by_key.pop(k)
    # decide first, write after: positions survive only on plates whose parts and meshes are the same
    keep_pos, lost = {}, []
    for pl in old['plates']:
        new = [p for p in D['plates'] if p['name'] == pl['name']]
        same = bool(new) and sorted(old['objects'][o]['base'] for o in pl['oids']) == sorted(D['objects'][k]['base'] for k in new[0]['keys'])
        for oid in pl['oids']:
            o = old['objects'][oid]; key = [k for k, v in old_by_key.items() if v[0] == oid][0]
            sb = (S or {}).get('objects', {}).get(key)
            moved = sb is not None and sb.get('transform') and any(abs(a - b) > 1e-3 for a, b in zip(sb['transform'], o['transform'] or []))
            if same and key in D['objects']:
                d = D['objects'][key]; V, F, _ = posed(d, stl_dir, sb.get('angle', packed_angle(d)) if sb else None); d['_sig'] = signature(V, F)
                last = sb['mesh'] if sb else signature(*part_mesh(old, oid, main_part(o)[0]))
                same_turn = abs((sb.get('angle', packed_angle(d)) if sb else packed_angle(d)) - packed_angle(d)) < 1e-6
                if same_mesh(d['_sig'], last) and same_turn: keep_pos[key] = (o['transform'], pl['name']); continue
            if moved: lost.append('%s moved by hand on "%s": the new layout replaces it' % (o['name'], pl['name']))
    conflicts = []
    for key, (oid, _) in old_by_key.items():
        if key not in D['objects']: continue
        o = old['objects'][oid]; d = D['objects'][key]; sb = (S or {}).get('objects', {}).get(key)
        _, _, kp, cf = merge4(sb.get('cfg_settings', sb['settings']) if sb else d['settings'], sb['settings'] if sb else d['settings'], d['settings'], o['settings'])
        for k, v in kp.items(): rep['kept'].append('%s: %s = %s in Bambu (config: %s)' % (o['name'], k, v[1], 'not set' if v[0] is None else v[0]))
        for k, v in cf.items(): conflicts.append('%s: %s was %s, config now %s, Bambu %s' % (o['name'], k, *v))
    gone = [old['objects'][oid]['name'] for key, (oid, _) in old_by_key.items() if key not in D['objects']
            and (S or {}).get('objects', {}).get(key) and (old['objects'][oid]['settings'] != S['objects'][key]['settings'])]
    lost += ['%s (removed from the configs) had settings changed in Bambu' % n for n in gone]
    rep['conflicts'] += conflicts; rep['lost'] = lost
    if dry:
        return rep
    if bambu_running():
        rep['refused'] = 'Bambu Studio is running: close it, then run this again'
        return rep
    if (lost or conflicts) and not accept_loss:
        rep['refused'] = 'a rebuild would lose manual edits (listed); fold them into the configs or pass accept_loss'
        return rep
    bambu3mf.build(proj, stl_dir, plate_list, settings, extra, filament, title, bed, project)
    for d in D['objects'].values(): d.pop('_sig', None); d.pop('_angle', None)     # the snapshot describes what was just built
    # put manual settings and kept positions back
    P = read(proj); M, why2 = match(P, D)
    if M is None: raise RuntimeError('rebuilt project does not match the configs: ' + why2)
    cfg = P['files'][MS].decode(); n_old, n_new = len(old['plates']), len(P['plates'])
    for key, oid in M.items():
        if key in old_by_key:
            ooid = old_by_key[key][0]; o, d = old['objects'][ooid], D['objects'][key]; sb = (S or {}).get('objects', {}).get(key)
            fin, _, _, _ = merge4(sb.get('cfg_settings', sb['settings']) if sb else d['settings'], sb['settings'] if sb else d['settings'], d['settings'], o['settings'])
            fe, _, _, _ = merge4({'filament': sb.get('cfg_extruder', sb['extruder']) if sb else d['extruder']}, {'filament': sb['extruder'] if sb else d['extruder']}, {'filament': d['extruder']}, {'filament': o['extruder']})
            cfg = set_object_meta(cfg, oid, fin, fe['filament']); P['objects'][oid]['settings'] = fin
        if key in keep_pos:
            t, pname = keep_pos[key]
            oi = [i for i, p in enumerate(old['plates']) if p['name'] == pname][0]; ni = [i for i, p in enumerate(P['plates']) if p['name'] == pname][0]
            (ax, ay), (bx, by) = plate_origin(oi, n_old, bed), plate_origin(ni, n_new, bed)
            t = list(t); t[9] += bx - ax; t[10] += by - ay
            cfg = set_transform(P, cfg, oid, t)
    # prime towers moved by hand stay on every plate that kept its name
    wt = {}
    for k in ('wipe_tower_x', 'wipe_tower_y'):
        ov, nv = old['ps'].get(k), P['ps'].get(k)
        if isinstance(ov, list) and isinstance(nv, list):
            for i, pl in enumerate(old['plates']):
                ni = [j for j, p in enumerate(P['plates']) if p['name'] == pl['name']]
                if ni and i < len(ov) and ni[0] < len(nv) and nv[ni[0]] != ov[i]: nv[ni[0]] = ov[i]; wt[pl['name']] = 1
            P['ps'][k] = nv
    if wt:
        P['files']['Metadata/project_settings.config'] = json.dumps(P['ps'], indent=4).encode()
        rep['kept'].append('prime tower kept on %d plates' % len(wt))
    # project overrides changed only in Bambu stay
    pb = project_base(S, old['ps'], project)
    pf, _, pkp, _ = merge3(pb, project_values(dict(old['ps'], **project), project), project_values(old['ps'], project))
    if pkp:
        bambu3mf.set_project(P['ps'], {k: old['ps'][k] for k in pkp})
        P['files']['Metadata/project_settings.config'] = json.dumps(P['ps'], indent=4).encode()
        rep['kept'] += ['project: %s changed in Bambu (kept)' % k for k in pkp]
    P['files'][MS] = cfg.encode()
    bambu3mf.save(proj, P['files'], P['infos'], zipfile.ZIP_STORED)
    _write_snapshot(proj, read(proj), D, M, project)
    rep['changed'] = True
    return rep


def print_report(proj, rep):
    print('==', os.path.basename(proj))
    for k, title in (('rebuild', 'rebuilt'), ('swapped', 'new mesh'), ('applied', 'config change applied'), ('kept', 'changed in Bambu, kept'),
                     ('relaid', 'laid out again'), ('conflicts', 'CONFLICT'), ('lost', 'would be lost'), ('notes', 'note'), ('refused', 'NOTHING WRITTEN')):
        v = rep.get(k)
        if not v: continue
        for line in (v if isinstance(v, list) else [v]): print('  %-24s %s' % (title + ':', line))
    if not any(rep.get(k) for k in ('rebuild', 'swapped', 'applied', 'kept', 'relaid', 'conflicts', 'refused')): print('  up to date')


def _xml(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def stamp(proj, meta, dry=False):
    """Set project metadata (e.g. {'Title': ..., 'Description': ...}) in 3D/3dmodel.model; Bambu shows them in the Project
    tab. Used for the Fusion version stamp (build_3mf.py). Returns the names changed; writes nothing while Bambu runs."""
    infos, files = bambu3mf.load(proj)
    model = files[MODEL].decode(); new = model
    for k, v in meta.items():
        tag = '<metadata name="%s">%s</metadata>' % (k, _xml(v))
        pat = re.compile(r'<metadata name="%s">.*?</metadata>' % re.escape(k), re.S)
        new = pat.sub(lambda _: tag, new, count=1) if pat.search(new) else new.replace('<resources>', tag + '\n <resources>', 1)
    changed = [k for k in meta if re.search(r'<metadata name="%s">(.*?)</metadata>' % re.escape(k), model, re.S) is None
               or re.search(r'<metadata name="%s">(.*?)</metadata>' % re.escape(k), model, re.S).group(1) != _xml(meta[k])]
    if changed and not dry:
        if bambu_running():
            raise RuntimeError('Bambu Studio is running: close it, then stamp again')
        files[MODEL] = new.encode()
        bambu3mf.save(proj, files, infos, zipfile.ZIP_STORED)
    return changed
