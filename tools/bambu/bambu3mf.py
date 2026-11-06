"""Read, build and edit Bambu Studio .3mf projects from scripts (stdlib only).
Run every edit through with_bambu_closed.py: saving in Bambu overwrites the file."""
import zipfile, re, struct, os, math, shutil, tempfile, json, uuid, sys, subprocess, glob, time

def studio_path():
    """The Bambu Studio executable (BAMBU_STUDIO overrides the search), or None."""
    env = os.environ.get('BAMBU_STUDIO')
    if env: return env
    for c in ('/Applications/BambuStudio.app/Contents/MacOS/BambuStudio', r'C:\Program Files\Bambu Studio\bambu-studio.exe',
              *glob.glob(os.path.expanduser('~/Applications/BambuStudio*.app/Contents/MacOS/BambuStudio'))):
        if os.path.exists(c): return c
    return shutil.which('bambu-studio') or shutil.which('BambuStudio')

def studio_running():
    if sys.platform == 'win32':
        return b'bambu-studio.exe' in subprocess.run(['tasklist', '/FI', 'IMAGENAME eq bambu-studio.exe'], capture_output=True).stdout.lower()
    return subprocess.run(['pgrep', '-x', 'BambuStudio' if sys.platform == 'darwin' else 'bambu-studio'], capture_output=True).returncode == 0

def load(proj):
    z = zipfile.ZipFile(proj); infos = z.infolist(); files = {i.filename: z.read(i.filename) for i in infos}; z.close()
    return infos, files

def save(proj, files, order, compress=zipfile.ZIP_DEFLATED):
    """order: zip infos or names, written in that order."""
    fd, tmp = tempfile.mkstemp(suffix='.tmp', dir=os.path.dirname(os.path.abspath(proj))); os.close(fd)   # same folder: the rename is atomic
    with zipfile.ZipFile(tmp, 'w', compress) as zo:
        for i in order: zo.writestr(i, files[i if isinstance(i, str) else i.filename])
    os.chmod(tmp, os.stat(proj).st_mode & 0o777 if os.path.exists(proj) else 0o644)
    os.replace(tmp, proj)

def backup(proj, keep=10):
    """Copy proj into .backups/ next to it, timestamped, keeping the newest `keep`. Call it before a build overwrites hand work."""
    if not os.path.exists(proj): return None
    d = os.path.join(os.path.dirname(os.path.abspath(proj)), '.backups'); os.makedirs(d, exist_ok=True); base = os.path.basename(proj)
    dst = os.path.join(d, time.strftime('%Y%m%d-%H%M%S-') + base); shutil.copy2(proj, dst)
    for old in sorted(f for f in os.listdir(d) if f.endswith('-' + base))[:-keep]: os.remove(os.path.join(d, old))
    return dst

def read_stl(p):
    """Binary STL -> (vertices, triangles). Weld on exact float32 values: rounding would merge distinct vertices."""
    d = open(p, 'rb').read(); n = struct.unpack('<I', d[80:84])[0]; vid = {}; V = []; F = []
    for i in range(n):
        o = 84 + i * 50 + 12; t = []
        for k in range(3):
            q = struct.unpack('<3f', d[o + 12 * k:o + 12 * k + 12])
            if q not in vid: vid[q] = len(V); V.append(q)
            t.append(vid[q])
        if len(set(t)) == 3: F.append(t)
    return V, F

def bbox(V): return [min(v[i] for v in V) for i in range(3)], [max(v[i] for v in V) for i in range(3)]

def volume(V, F):
    s = 0.0
    for a, b, c in F:
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = V[a], V[b], V[c]; s += x0 * (y1 * z2 - z1 * y2) - y0 * (x1 * z2 - z1 * x2) + z0 * (x1 * y2 - y1 * x2)
    return s / 6

def mesh_xml(V, F):   # %.9g: 6 digits made non-manifold edges
    return ('\n     <vertices>\n' + ''.join('      <vertex x="%.9g" y="%.9g" z="%.9g"/>\n' % tuple(v) for v in V) + '     </vertices>\n     <triangles>\n'
            + ''.join('      <triangle v1="%d" v2="%d" v3="%d"/>\n' % tuple(t) for t in F) + '     </triangles>\n    ')

def plates(cfg, prefix, exact=False):
    """<plate> blocks whose plater_name starts with prefix ('' = every plate), or equals it when exact."""
    pat = r'plater_name" value="%s%s"' % (re.escape(prefix), '' if exact else '[^"]*')
    return [p for p in re.findall(r'<plate>(.*?)</plate>', cfg, re.S) if re.search(pat, p)]

def objects(cfg, plate_prefix='', exact=False):
    """[(object id, <object> body, name)] — on the matching plates, in plate order."""
    ids = list(dict.fromkeys(o for pl in plates(cfg, plate_prefix, exact) for o in re.findall(r'object_id" value="(\d+)"', pl)))
    out = []
    for oid in ids:
        body = re.search(r'<object id="%s">(.*?)</object>' % oid, cfg, re.S).group(1)
        out.append((oid, body, re.search(r'name" value="([^"]*)', body.split('<part')[0]).group(1)))
    return out

def set_settings(proj, prefix, kvs, plate_prefix='', exact=False):
    """Per-object settings: Bambu resets global overrides of a system preset on load, per-object ones stay."""
    infos, files = load(proj); cfg = files['Metadata/model_settings.config'].decode(); hit = []
    for oid, body, name in objects(cfg, plate_prefix, exact):
        if not name.startswith(prefix): continue
        head = body.split('<part')[0]; nh = head
        for k, v in kvs:
            if re.search(r'key="%s" value="[^"]*"' % re.escape(k), nh): nh = re.sub(r'key="%s" value="[^"]*"' % re.escape(k), 'key="%s" value="%s"' % (k, v), nh)
            else: nh = nh.replace('<metadata key="extruder"', '<metadata key="%s" value="%s"/>\n    <metadata key="extruder"' % (k, v), 1)
        cfg = cfg.replace('<object id="%s">' % oid + body, '<object id="%s">' % oid + nh + body[len(head):]); hit.append(name)
    if hit:
        files['Metadata/model_settings.config'] = cfg.encode(); save(proj, files, infos, zipfile.ZIP_STORED)
    return hit

def update_meshes(proj, stl_dir, plate_prefix=''):
    """Swap changed meshes for objects on the matching plates whose name matches an STL in stl_dir (same name, or the part name before ' - ').
    Position, rotation, per-object settings and pauses are kept: the new mesh keeps the old bbox centre (xy) and bed contact (z)."""
    key = lambda n: n.split(' - ')[0].replace('.stl', '')
    stls = {key(f): os.path.join(stl_dir, f) for f in os.listdir(stl_dir) if f.endswith('.stl')}
    infos, files = load(proj); cfg = files['Metadata/model_settings.config'].decode(); model = files['3D/3dmodel.model'].decode(); changed = []
    for oid, body, name in objects(cfg, plate_prefix):
        src = stls.get(key(name))
        if not src: continue
        if len(re.findall(r'<part id="(\d+)" subtype="normal_part"', body)) != 1: print('skip (several parts):', name); continue
        comp = re.search(r'<object id="%s"[^>]*>\s*<components>\s*<component p:path="([^"]+)" objectid="(\d+)"' % oid, model)
        path, mid = comp.group(1).lstrip('/'), comp.group(2); om = files[path].decode()
        blk = re.search(r'(<object id="%s"[^>]*>\s*<mesh>)(.*?)(</mesh>)' % mid, om, re.S)
        old = [tuple(float(x) for x in q) for q in re.findall(r'<vertex x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"', blk.group(2))]
        oldF = [tuple(int(x) for x in t) for t in re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', blk.group(2))]
        V, F = read_stl(src); (olo, ohi), (nlo, nhi) = bbox(old), bbox(V)
        # unchanged = same triangle count, same bbox size, volume within 0.001 mm³ (absolute: a relative check once skipped a 10 mm³ change)
        if len(oldF) == len(F) and abs(volume(old, oldF) - volume(V, F)) < 1e-3 and all(abs((ohi[i] - olo[i]) - (nhi[i] - nlo[i])) < 1e-3 for i in range(3)): continue
        oc = [(olo[i] + ohi[i]) / 2 for i in range(2)] + [olo[2]]; nc = [(nlo[i] + nhi[i]) / 2 for i in range(2)] + [nlo[2]]
        off = [oc[i] - nc[i] for i in range(3)]
        files[path] = (om[:blk.start(2)] + mesh_xml([(x + off[0], y + off[1], z + off[2]) for x, y, z in V], F) + om[blk.end(2):]).encode()
        cfg = cfg.replace(body, re.sub(r'face_count="\d+"', 'face_count="%d"' % len(F), body)); changed.append(name)
    if changed:
        files['Metadata/model_settings.config'] = cfg.encode(); save(proj, files, infos, zipfile.ZIP_STORED)
    return changed

NS = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p"'

# rotations that put the named model face on the bed (its outward normal → −z)
ORIENT = {False: lambda x, y, z: (x, y, z), True: lambda x, y, z: (x, -y, -z), '-z': lambda x, y, z: (x, y, z), '+z': lambda x, y, z: (x, -y, -z),
          '+y': lambda x, y, z: (x, z, -y), '-y': lambda x, y, z: (x, -z, y), '+x': lambda x, y, z: (z, y, -x), '-x': lambda x, y, z: (-z, y, x)}

def prep_object(o, stl_dir):
    """Read an object's STL, put its bed face down (orient), centre it on (0, 0, 0). Returns V, F, size, support blockers."""
    f, flip = o[1], o[2]
    V, F = read_stl(os.path.join(stl_dir, f))           # an absolute path works too
    face, turn = (flip.split(',') + [''])[:2] if isinstance(flip, str) else (flip, '')
    def xf(V):
        if isinstance(face, str) and face.startswith('n'): V = [normal_down([float(x) for x in face[1:].split('/')], v) for v in V]
        else: V = [ORIENT[face](*v) for v in V]     # proper rotations keep the winding
        if turn.startswith('z'):
            a = math.radians(float(turn[1:])); ca, sa = math.cos(a), math.sin(a); V = [(ca * x - sa * y, sa * x + ca * y, z) for x, y, z in V]
        return V
    V = xf(V); lo, hi = bbox(V); c = [(lo[i] + hi[i]) / 2 for i in range(3)]
    bl = []       # support blockers: boxes (lo, hi) in the STL's own (model) coordinates, moved with the part
    for blo, bhi, *frame in (o[3].get('_blockers', []) if len(o) > 3 else []):
        P = [(x, y, z) for x in (blo[0], bhi[0]) for y in (blo[1], bhi[1]) for z in (blo[2], bhi[2])]
        if frame:   # (origin, (ux, uy, uz)): the box is given in that frame (orthonormal axes, model coordinates)
            org, ax = frame[0]; P = [tuple(org[i] + sum(p[k] * ax[k][i] for k in range(3)) for i in range(3)) for p in P]
        BV = xf(P)
        bl.append(([(x - c[0], y - c[1], z - c[2]) for x, y, z in BV], BOX_FACES))
    return [(x - c[0], y - c[1], z - c[2]) for x, y, z in V], F, [hi[i] - lo[i] for i in range(3)], bl

def pack_plates(objs, stl_dir, bed, margin=5.0, gap=3.0, cache=None, tall=16):   # 5 mm from the bed edge, 3 between parts: checked by slicing
    """Pack objects as tightly as their real footprints allow (pack.py); more plates when full.
    Returns [[(object, (x, y, angle)), …] per plate] — use each as the plate's objects with lay {'_placed': [positions]}.
    cache: a JSON file; a layout is reused while the STLs, orientations and spacing stay the same (packing takes a minute)."""
    import hashlib
    h = hashlib.sha1(repr((bed, margin, gap, 'holes filled', 'tall +8 if it fits') + ((tall,) if tall != 16 else ())).encode())
    for o in objs: h.update(open(os.path.join(stl_dir, o[1]), 'rb').read()); h.update(repr((o[0], o[2])).encode())
    key = h.hexdigest()[:16]; store = json.load(open(cache)) if cache and os.path.exists(cache) else {}
    if key in store: res = store[key]
    else:
        import pack
        fps = []
        for o in objs:      # tall parts: their tree supports branch out past the outline, so 8 mm more room around them
            V, F, size, _ = prep_object(o, stl_dir); f = pack.footprint(V, F, gap + (tall if size[2] > 40 else 0))
            if not pack.fits(f, bed, margin): f = pack.footprint(V, F, gap)      # no room for the extra
            fps.append(f)
        res = pack.pack(fps, bed, margin)
        if cache: store[key] = res; json.dump(store, open(cache, 'w'), indent=0)
    out = []
    for o, (k, x, y, a) in zip(objs, res):
        while len(out) <= k: out.append([])
        out[k].append((o, (x, y, a)))
    return out

def bambu_name(n):
    """Bambu refuses names with < > : / \\ | ? * " (an error dialog on open): ':' → ' -', '/' → '-', the rest dropped."""
    n = n.replace(': ', ' - ').replace(':', '-').replace('/', '-')
    return ''.join(c for c in n if c not in '<>\\|?*"')

BOX_FACES = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]   # corners x-major (x, y, z ∈ {lo, hi})

def normal_down(n, v):
    """Rotate point v by the rotation that turns the unit vector n to (0, 0, -1) (Rodrigues; a proper rotation)."""
    l = math.sqrt(sum(c * c for c in n)); n = [c / l for c in n]
    k = (-n[1], n[0], 0.0); s = math.hypot(*k); c = -n[2]               # axis n × (0,0,-1), sin, cos
    if s < 1e-9: return v if c > 0 else (v[0], -v[1], -v[2])
    k = [x / s for x in k]; d = sum(k[i] * v[i] for i in range(3)); kx = (k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0])
    return tuple(v[i] * c + kx[i] * s + k[i] * d * (1 - c) for i in range(3))

def purge_stl(path, tube_mm, thickness=1.2, filament_d=1.75):
    """A flat solid square that uses up tube_mm of filament — print it as its own plate right before a part when the
    filament sitting in the (unheated) feed tube is wet. Returns the side length in mm."""
    vol = tube_mm * math.pi * (filament_d / 2) ** 2; a = math.sqrt(vol / thickness); h = thickness
    v = [(x, y, z) for x in (0, a) for y in (0, a) for z in (0, h)]
    q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [t for a_, b, c, d in q for t in ((a_, b, c), (a_, c, d))]
    with open(path, 'wb') as f:
        f.write(b'\0' * 80 + struct.pack('<I', len(tris)))
        for t in tris: f.write(struct.pack('<12fH', 0, 0, 0, *v[t[0]], *v[t[1]], *v[t[2]], 0))
    return a

FILAMENT_KEYS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'filament_keys.json')))   # key -> values per filament

def add_filament(ps, src):
    """Append the first filament of project settings src to project settings ps (same printer / nozzle). Returns its slot (1-based)."""
    n = len(ps['filament_settings_id'])
    for k, stride in FILAMENT_KEYS.items():
        if k not in ps or k not in src: continue
        if k == 'filament_self_index': ps[k] += [str(n + 1)] * stride; continue
        ps[k] += src[k][:stride]
    m = ps['flush_volumes_matrix']; size = n + 1                     # one material per plate here: flushing barely matters
    ps['flush_volumes_matrix'] = [m[r * n + c] if r < n and c < n else ('0' if r == c else '300') for r in range(size) for c in range(size)]
    ps['filament_dev_ams_drying_ams_limitations'] = ps.get('filament_dev_ams_drying_ams_limitations', []) + src.get('filament_dev_ams_drying_ams_limitations', [])[:2]
    d = ps.get('different_settings_to_system')
    if d: d.insert(len(d) - 1, (src.get('different_settings_to_system') or ['', ''])[1])
    return n + 1

PRINTER_KEYS = ('machine_start_gcode', 'machine_end_gcode', 'layer_change_gcode', 'change_filament_gcode')

def set_project(ps, values):
    """Global overrides in project_settings.config. Each key is also listed in different_settings_to_system — the process slot
    [0], or the printer slot [-1] for the machine G-code templates — as Bambu does for an edited preset; otherwise Bambu resets it
    to the system preset on load."""
    for k, v in values.items():
        if k not in ps: print('WARNING: %s is not in this project: a typo, or a key this printer does not have' % k)
        ps[k] = [v] * len(ps[k]) if isinstance(ps.get(k), list) else v
    d = ps.get('different_settings_to_system') or ['']
    for slot, ks in ((0, [k for k in values if k not in PRINTER_KEYS]), (len(d) - 1, [k for k in values if k in PRINTER_KEYS])):
        keys = [x for x in d[slot].split(';') if x]; keys += [k for k in ks if k not in keys]
        d[slot] = ';'.join(sorted(keys))
    ps['different_settings_to_system'] = d

def set_filament(ps, slot, values):
    """Overrides of one filament slot (1-based) in project_settings.config: only that slot's entry of each list key changes,
    and the keys go into different_settings_to_system[slot] (the filament slots sit between the process [0] and the printer
    [-1]), as Bambu does for an edited filament preset. Returns the keys that actually changed."""
    changed = []
    for k, v in values.items():
        if not isinstance(ps.get(k), list) or len(ps[k]) < slot:
            raise KeyError('%s: not a filament list key with slot %d' % (k, slot))
        if ps[k][slot - 1] != v:
            ps[k][slot - 1] = v; changed.append(k)
    d = ps.get('different_settings_to_system') or ['']
    while len(d) <= slot: d.append('')
    keys = [x for x in d[slot].split(';') if x]; keys += [k for k in values if k not in keys]
    d[slot] = ';'.join(sorted(keys)); ps['different_settings_to_system'] = d
    return changed

SLOW_COOL = ';===== slow bed cool-down (PA6-CF warps when the bed goes cold at once) ====='

# the bed was at print temperature the whole print, so no hold there: step down to where the material sets (30 min in all)
COOL_STEPS = ((80, 420), (65, 360), (50, 360), (40, 360), (30, 300))   # 30 min

def remove_slow_cooldown(end_gcode):
    """Undo slow_cooldown(): the original end G-code (bed off right after the print)."""
    if SLOW_COOL not in end_gcode: return end_gcode
    i = end_gcode.index(SLOW_COOL); k = end_gcode.rfind('\n', 0, i) + 1
    prev = end_gcode.rfind('\n', 0, k - 1) + 1                      # an {if …} line in front belongs to the block
    if end_gcode[prev:k].startswith('{if filament_type'): k = prev
    j = end_gcode.index(';=====printer finish air purification=========', i)
    g = end_gcode[:k] + end_gcode[j:]
    return g.replace('; (bed stays on: slow cool-down below)', 'M140 S0 ; turn off bed', 1)

def slow_cooldown(end_gcode, steps=COOL_STEPS, only_for=None):
    """End G-code that keeps the bed hot after the print and lowers it in steps (temperature °C or None = keep, seconds),
    before the printer's air purification runs its fans. only_for = a filament type (e.g. 'PA6-CF'): in a mixed project the
    cool-down runs only when the plate ends on that filament, other plates switch the bed off as before.
    An already patched template is re-patched with the current steps."""
    end_gcode = remove_slow_cooldown(end_gcode)
    assert 'M140 S0 ; turn off bed' in end_gcode and ';=====printer finish air purification=========' in end_gcode
    g = end_gcode.replace('M140 S0 ; turn off bed', '; (bed stays on: slow cool-down below)', 1)
    block = [SLOW_COOL] + ['%sM400 S%d' % ('' if t is None else 'M140 S%d\n' % t, sec) for t, sec in steps] + ['M140 S0 ; turn off bed', '']
    if only_for:
        block = ['{if filament_type[current_extruder] == "%s"}' % only_for] + block[:-1] + ['{else}', 'M140 S0 ; turn off bed', '{endif}', '']
    return g.replace(';=====printer finish air purification=========', '\n'.join(block) + ';=====printer finish air purification=========', 1)

def bed_size(ps):
    """Bed width in mm from project settings (printable_area is a list of 'XxY' corners)."""
    return max(float(c.split('x')[0]) for c in ps['printable_area'])

def filament_sequence(n_plates):
    return json.dumps({'plate_%d' % (i + 1): {'nozzle_sequence': [], 'optimal_assignment': [], 'sequence': []} for i in range(n_plates)}, separators=(',', ':')).encode()

def filament_names(ps):
    return list(ps['filament_settings_id'])

def only_filament(ps, name, colours=None):
    """Project settings reduced to one template filament (exact preset name, or a unique part of it), then one slot per colour.
    Every per-filament list has its own stride (FILAMENT_KEYS); filament_self_index keeps one entry per extruder variant:
    a short list loads in the CLI but is 'Invalid configuration file' in the GUI."""
    ids = ps['filament_settings_id']; hits = [i for i, f in enumerate(ids) if f == name] or [i for i, f in enumerate(ids) if name.lower() in f.lower()]
    if len(set(ids[i] for i in hits)) != 1: raise ValueError('filament %r: pick one of %s' % (name, sorted(set(ids))))
    i = hits[0]; n = len(ids); one = json.loads(json.dumps(ps))
    for k, stride in FILAMENT_KEYS.items():
        if k in one and isinstance(one[k], list) and len(one[k]) == n * stride: one[k] = one[k][i * stride:(i + 1) * stride]
    if 'filament_self_index' in one: one['filament_self_index'] = ['1'] * FILAMENT_KEYS['filament_self_index']
    one['flush_volumes_matrix'] = ['0']
    lim = one.get('filament_dev_ams_drying_ams_limitations')
    if lim and len(lim) == 2 * n: one['filament_dev_ams_drying_ams_limitations'] = lim[2 * i:2 * i + 2]
    d = one.get('different_settings_to_system')
    if d and len(d) == n + 2: one['different_settings_to_system'] = [d[0], d[1 + i], d[-1]]
    if colours:
        src = json.loads(json.dumps(one))
        for _ in colours[1:]: add_filament(one, src)
        one['filament_colour'] = list(colours)
    return one

def new_project(template, proj, filament=None, colours=None):
    """Copy a template to proj, optionally down to one filament in the given colours. Then call build(proj, ...)."""
    infos, files = load(template)
    if filament:
        files['Metadata/project_settings.config'] = json.dumps(only_filament(json.loads(files['Metadata/project_settings.config']), filament, colours), indent=4).encode()
    elif colours:
        ps = json.loads(files['Metadata/project_settings.config']); ps['filament_colour'] = list(colours) + ps['filament_colour'][len(colours):]
        files['Metadata/project_settings.config'] = json.dumps(ps, indent=4).encode()
    save(proj, files, infos)

def plate_positions(sizes, lay, bed):
    """Centre (x, y) of each object on its plate: packed positions (lay['_placed']), or rows of lay['cols'] objects (or lay['rows']
    counts), each row centred in X, rows going down in Y, the whole block centred on the plate. sizes: [(x, y, z)] per object."""
    if '_placed' in lay: return {k: (x, y) for k, (x, y, _) in enumerate(lay['_placed'])}
    per_row, gap = lay.get('cols', 1), lay.get('gap', 10)
    counts = lay.get('rows') or [per_row] * math.ceil(len(sizes) / per_row); rows, i = [], 0
    for k in counts: rows.append(list(range(i, min(i + k, len(sizes))))); i += k
    pos = {}
    rh = [max(sizes[i][1] for i in r) for r in rows]; y = bed / 2 + (sum(rh) + gap * (len(rows) - 1)) / 2
    for r, h in zip(rows, rh):
        x = bed / 2 - (sum(sizes[i][0] for i in r) + gap * (len(r) - 1)) / 2
        for i in r: pos[i] = (x + sizes[i][0] / 2, y - h / 2); x += sizes[i][0] + gap
        y -= h + gap
    return pos

def build(proj, stl_dir, plate_list, settings=None, extra=None, filament=1, title='', bed=None, project=None):
    """Rebuild every object and plate of proj from STLs; printer / process / filament presets stay as they are in proj.
    plate_list: [(plate name, [(object name, stl file, orient[, {per-object settings, '_filament': slot}])])];
    settings: per-object for all, extra: {object name: {more settings}}; the optional 4th item of an object wins over both.
    project: global process overrides (set_project) — use these for everything that is the same on every part; settings / extra
    are written per object, only for real per-part exceptions.
    orient: False = as modelled, True = flipped 180° about X, or the model face that goes on the bed: '+x', '-x', '+y', '-y', '+z' (= True);
    add ',z<deg>' to turn it on the bed afterwards ('+y,z90', '+y,z26.6'). For a tilted bed face give its outward normal
    as 'n<x>/<y>/<z>' ('n0/-0.994/0.113'): the part is rotated so that this normal points down. A plate may carry a third item {'cols': n, 'gap': mm}: n objects per row
    (default 1, a single column along Y) or {'rows': [2, 3]} for objects per row, gap between objects (default 10).
    Per-object dict keys starting with '_': '_filament': slot, '_blockers': [((x0, y0, z0), (x1, y1, z1)), …] support-blocker boxes
    (model coordinates; a third element (origin, (ux, uy, uz)) gives the box in a rotated frame, e.g. along a tilted bore)
    in the STL's coordinates."""
    _, keep = load(proj)
    ps0 = json.loads(keep['Metadata/project_settings.config']); bed = bed or bed_size(ps0)
    height = float(ps0.get('printable_height', 1e9))     # Bambu stores numbers as strings
    extra = {bambu_name(k): v for k, v in (extra or {}).items()}; stride = bed * 1.2   # Bambu lays plates out on a grid, 20 % gap between them
    files = {k: v for k, v in keep.items() if k in ('[Content_Types].xml', '_rels/.rels', 'Metadata/slice_info.config', 'Metadata/filament_sequence.json', 'Metadata/project_settings.config')}
    settings = settings or {}
    if project:
        ps = json.loads(files['Metadata/project_settings.config']); set_project(ps, project)
        files['Metadata/project_settings.config'] = json.dumps(ps, indent=4).encode()
    cols = math.ceil(math.sqrt(len(plate_list)) - 1e-9)
    res, items, objcfg, platecfg, asm, rels = [], [], [], [], [], []; oid = 0
    for pi, (pname, objs, *lay) in enumerate(plate_list):
        pname = bambu_name(pname); objs = [(bambu_name(o[0]),) + tuple(o[1:]) for o in objs]
        lay = lay[0] if lay else {}; per_row, gap = lay.get('cols', 1), lay.get('gap', 10)
        ox, oy = (pi % cols) * stride, -(pi // cols) * stride
        meshes, blockers = [], []
        for k, o in enumerate(objs):
            V, F, size, bl = prep_object(o, stl_dir)
            if '_placed' in lay:                                  # packed by pack_plates(): turn about the part's centre
                a = math.radians(lay['_placed'][k][2]); ca, sa = math.cos(a), math.sin(a)
                rz = lambda P: [(ca * x - sa * y, sa * x + ca * y, z) for x, y, z in P]
                V = rz(V); bl = [(rz(BV), BF) for BV, BF in bl]; lo, hi = bbox(V); size = [hi[i] - lo[i] for i in range(3)]
            meshes.append((V, F, size)); blockers.append(bl)
        pos = plate_positions([m[2] for m in meshes], lay, bed)
        inst = []
        for k, (o, (V, F, size)) in enumerate(zip(objs, meshes)):
            name, f, flip = o[:3]; per = dict(o[3]) if len(o) > 3 else {}; fil = per.pop('_filament', filament); per.pop('_blockers', None)
            bl = blockers[k]
            oid += 1; mesh_id, obj_id = 2 * oid - 1, 2 * oid; path = '3D/Objects/object_%d.model' % oid
            tr = '1 0 0 0 1 0 0 0 1 %.6f %.6f %.6f' % (ox + pos[k][0], oy + pos[k][1], size[2] / 2)
            if max(size[0], size[1]) > bed - 4: print('WARNING: %s is %.1f × %.1f' % (name, size[0], size[1]))
            if size[2] > height: print('WARNING: %s is %.1f tall, the printer takes %.1f' % (name, size[2], height))
            sub_ids = [10000 + 100 * oid + j for j in range(len(bl))]      # extra mesh ids for the blocker volumes
            files[path] = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" %s>\n <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n  <object id="%d" p:UUID="%s" type="model">\n   <mesh>%s</mesh>\n  </object>\n%s </resources>\n <build/>\n</model>\n'
                           % (NS, mesh_id, uuid.uuid4(), mesh_xml(V, F),
                              ''.join('  <object id="%d" p:UUID="%s" type="model">\n   <mesh>%s</mesh>\n  </object>\n' % (sid, uuid.uuid4(), mesh_xml(BV, BF)) for sid, (BV, BF) in zip(sub_ids, bl)))).encode()
            rels.append(' <Relationship Target="/%s" Id="rel-%d" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n' % (path, oid))
            res.append('  <object id="%d" p:UUID="%s" type="model">\n   <components>\n%s   </components>\n  </object>\n' % (obj_id, uuid.uuid4(),
                       ''.join('    <component p:path="/%s" objectid="%d" p:UUID="%s" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n' % (path, mid, uuid.uuid4()) for mid in [mesh_id] + sub_ids)))
            items.append('  <item objectid="%d" p:UUID="%s" transform="%s" printable="1"/>\n' % (obj_id, uuid.uuid4(), tr))
            opts = ''.join('    <metadata key="%s" value="%s"/>\n' % kv for kv in {**settings, **extra.get(name, {}), **per}.items())
            objcfg.append('  <object id="%d">\n    <metadata key="name" value="%s"/>\n' % (obj_id, name) + opts +
                          '    <metadata key="extruder" value="%d"/>\n    <metadata face_count="%d"/>\n    <part id="%d" subtype="normal_part">\n      <metadata key="name" value="%s"/>\n      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n      <mesh_stat face_count="%d" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n    </part>\n%s  </object>\n' % (fil, len(F) + 12 * len(bl), mesh_id, name, len(F),
                          # part id = the component's objectid, as Bambu writes it: with ids 1, 2, 3 Bambu ignored the subtype and
                          # printed the blocker boxes as solid parts, a slab over the screw pockets
                          ''.join('    <part id="%d" subtype="support_blocker">\n      <metadata key="name" value="support blocker %d"/>\n      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n      <mesh_stat face_count="12" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n    </part>\n' % (sid, j + 1) for j, sid in enumerate(sub_ids))))
            inst.append('    <model_instance>\n      <metadata key="object_id" value="%d"/>\n      <metadata key="instance_id" value="0"/>\n      <metadata key="identify_id" value="%d"/>\n    </model_instance>\n' % (obj_id, 100 + oid))
            asm.append('   <assemble_item object_id="%d" instance_id="0" transform="%s" offset="0 0 0" />\n' % (obj_id, tr))
            print('%-18s %-45s %-9s %.1f × %.1f × %.1f' % (pname, name, '' if not flip else 'flipped' if flip is True else str(flip) + ' down', *size))
        platecfg.append('  <plate>\n    <metadata key="plater_id" value="%d"/>\n    <metadata key="plater_name" value="%s"/>\n    <metadata key="locked" value="false"/>\n    <metadata key="filament_map_mode" value="Auto For Flush"/>\n%s  </plate>\n' % (pi + 1, pname, ''.join(inst)))
    files['Metadata/filament_sequence.json'] = filament_sequence(len(plate_list))   # one entry per plate, or Bambu keeps the template's plates
    files['3D/3dmodel.model'] = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" %s>\n <metadata name="Application">BambuStudio-02.08.02.61</metadata>\n <metadata name="BambuStudio:3mfVersion">1</metadata>\n <metadata name="Title">%s</metadata>\n <resources>\n%s </resources>\n <build p:UUID="%s">\n%s </build>\n</model>\n' % (NS, title, ''.join(res), uuid.uuid4(), ''.join(items))).encode()
    files['3D/_rels/3dmodel.model.rels'] = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n%s</Relationships>\n' % ''.join(rels)).encode()
    files['Metadata/model_settings.config'] = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n%s%s  <assemble>\n%s  </assemble>\n</config>\n' % (''.join(objcfg), ''.join(platecfg), ''.join(asm))).encode()
    order = ['[Content_Types].xml', '_rels/.rels', '3D/3dmodel.model', '3D/_rels/3dmodel.model.rels'] + sorted(k for k in files if k.startswith('3D/Objects/')) + sorted(k for k in files if k.startswith('Metadata/'))
    save(proj, files, order); print('wrote', proj)
