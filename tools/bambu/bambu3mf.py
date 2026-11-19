"""Read, build and edit Bambu Studio .3mf projects from scripts (stdlib only).
Run every edit through with_bambu_closed.py: saving in Bambu overwrites the file."""
import zipfile, re, struct, os, math, shutil, tempfile, json, uuid, sys, subprocess, glob, time, importlib.util, inspect

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
    """order: zip infos or names, written in that order.
    In a gated project (a gate/project.json above proj) the gate's guard sees the meshes first: in enforce mode it raises
    gate_guard.GuardRefusal for a new mesh without a valid stamp and nothing is written. The guard also compares the fit
    settings (hole compensation, layer height, ...: global values and per-object overrides) of `files` with the file on
    disk: a change this process did not name with note_setting() is refused in enforce mode and is one loud line in
    observe mode. set_project(), set_settings(), set_filament() and build() name what they are asked to set.
    A note lives for ONE guarded save: whatever the guard says, the notes are gone afterwards, so a setting named for
    one write never excuses a change in a later one. A note made for a project file (note_setting(..., project=))
    counts only at a save of that file.
    After the write the meshes go to the print ledger (when a bambulab/ledger/ folder is above proj). With neither
    above proj nothing of this runs."""
    try: state = _before_save(proj, files)
    finally:
        if _roots(proj)[0]: del _SETTING_NOTES[:]          # asked for this save, not for the next one
    fd, tmp = tempfile.mkstemp(suffix='.tmp', dir=os.path.dirname(os.path.abspath(proj))); os.close(fd)   # same folder: the rename is atomic
    with zipfile.ZipFile(tmp, 'w', compress) as zo:
        for i in order: zo.writestr(i, files[i if isinstance(i, str) else i.filename])
    os.chmod(tmp, os.stat(proj).st_mode & 0o777 if os.path.exists(proj) else 0o644)
    os.replace(tmp, proj)
    if state: record_writes(proj, state['meshes'], state['stamps'], _caller())

# ---- the gate and the print ledger: nothing below runs unless gate/project.json or bambulab/ledger/ lies above the .3mf ----

_HERE = os.path.dirname(os.path.abspath(__file__))
_NOTES = {}        # object name -> the STLs this process read for it, newest last (note_stl)
_SETTING_NOTES = []        # the slicer settings this process means to change (note_setting)

class GuardRefusal(Exception):
    """Raised by save() in a gated project in enforce mode when the gate's guard cannot be loaded at all: what was not
    checked has not passed. (A refusal of the guard itself is gate_guard.GuardRefusal.)"""

def note_setting(key, value=None, scope='global', tool=None, project=None):
    """Tell the gate's guard that this process MEANS to change a slicer setting, so that the next save() does not take
    the change for an accident: key = the setting, value = what it is set to (None = any value, also its removal),
    scope = 'global' (project_settings.config), an object name (a per-object override) or '*' (anywhere),
    project = the .3mf the change is for (None = the next guarded save, whatever file it writes).
    set_project(), set_filament(), set_settings() and build() call this for what they are asked to set; a script that
    edits a setting by its own means calls it too. A note covers only that key, in that scope, at that value: a fit
    setting that changes to anything else at a save is still reported. It is used up by the next guarded save.
    Never raises."""
    try:
        if isinstance(value, (list, tuple)): value = [str(x) for x in value]
        elif value is not None: value = str(value)
        _SETTING_NOTES.append({'key': str(key), 'value': value, 'scope': scope if scope in ('global', '*') else bambu_name(str(scope)), 'tool': tool,
                               'project': os.path.realpath(project) if project else None})
        del _SETTING_NOTES[:-4000]
    except Exception: pass

def _enforce_by_text(gate):
    """Does gate/project.json say "mode": "enforce"? Read from its text: this runs when the gate tools cannot be loaded."""
    try:
        with open(os.path.join(gate, 'gate', 'project.json'), encoding='utf-8', errors='replace') as f:
            return re.search(r'"mode"\s*:\s*"enforce"', f.read()) is not None
    except Exception: return False

def note_stl(name, stl, orient=None, model_stl=None, tool=None):
    """Remember which STL the mesh of an object is made from, so that the gate's guard can find its stamp and the ledger
    its model: the path, the orientation the library applied to it (False = as it lies in the file) and, when the STL is
    itself a turned copy, the model-frame STL it came from. prep_object() and update_meshes() call this; a script that
    places a mesh by its own means calls it too. The file is hashed (sha256, geometry) only at a save in a gated project,
    and a note is used only for a mesh of the same triangle count, volume and area. Never raises."""
    try:
        notes = _NOTES.setdefault(bambu_name(str(name)), [])
        notes.append({'stl': os.path.abspath(stl), 'orient': orient, 'model_stl': os.path.abspath(model_stl) if model_stl else None, 'tool': tool})
        del notes[:-8]
    except Exception: pass

def _roots(proj):
    """(folder with gate/project.json, folder with bambulab/ledger/): the nearest ones above proj, or None."""
    d = os.path.dirname(os.path.abspath(proj)); gate = ledger = None
    while True:
        if gate is None and os.path.isfile(os.path.join(d, 'gate', 'project.json')): gate = d
        if ledger is None and os.path.isdir(os.path.join(d, 'bambulab', 'ledger')): ledger = d
        up = os.path.dirname(d)
        if up == d or (gate and ledger): return gate, ledger
        d = up

def _tool(name, *paths):
    """A helper module loaded from the first of these files that exists (kept in sys.modules under its plain name), or None."""
    if name in sys.modules: return sys.modules[name]
    for p in paths:
        p = os.path.normpath(p)
        if not os.path.isfile(p): continue
        if os.path.dirname(p) not in sys.path: sys.path.append(os.path.dirname(p))     # it imports its own neighbours
        spec = importlib.util.spec_from_file_location(name, p); mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
        try: spec.loader.exec_module(mod)
        except BaseException: sys.modules.pop(name, None); raise
        return mod
    return None

def _caller():
    """Name of the function that called save(): the "tool" of a ledger line."""
    try:
        f = sys._getframe(2); name = f.f_code.co_name
        return name if name != '<module>' else os.path.basename(f.f_code.co_filename)
    except Exception: return None

def project_meshes(files):
    """One entry per printable-part object of the archive `files` (modifiers and support blockers left out), as the gate's
    guard and the ledger take them: {"object", "plate", "plate_index", "printable", "vertices", "triangles"} from
    meshid.project_meshes(), plus "stl" (the model-frame STL the mesh was made from in this process, or None when it was
    only carried), "stl_sha256", "model" (meshid.geom_hash of that STL), "orient" and "placed_from" (the file that was
    read, when that is a turned copy of "stl")."""
    meshid = _tool('meshid', os.path.join(_HERE, 'meshid.py')); ids, clouds = {}, {}
    def of(path):
        if path not in ids: ids[path] = meshid.stl_ids(path)
        return ids[path]
    def copy_of(path, mesh):
        """Is the placed mesh that STL moved as a rigid body? Proven vertex by vertex (meshid.rigid_copy): triangle count,
        volume and area agree for a part whose hole moved, and such a mesh must not travel under the stamp of the STL."""
        if path not in clouds: clouds[path] = meshid.read_stl(path)[0]
        return meshid.rigid_copy(clouds[path], mesh['vertices'])
    meshes = meshid.project_meshes(files)
    for m in meshes:
        m.update(stl=None, stl_sha256=None, model=None, orient=None, placed_from=None); shape = None
        for n in reversed(_NOTES.get(m['object'], [])):
            try:
                shape = shape or meshid.shape_id(m['vertices'], m['triangles'])
                if not meshid.same_shape(of(n['stl'])['shape_id'], shape, 1e-4, box=False): continue      # not the mesh that note is about
                if not copy_of(n['stl'], m): continue                                                     # same numbers, another geometry
                src = n['stl']
                if n['model_stl'] and n['model_stl'] != n['stl']:
                    if meshid.same_shape(of(n['model_stl'])['shape_id'], shape, 1e-4, box=False) and copy_of(n['model_stl'], m): src = n['model_stl']
                    else: print('gate: %s was placed from %s, which is NOT a turned copy of the model STL named for it (%s): the mesh is tied to the file it '
                                'was read from, not to that STL' % (m['object'], os.path.basename(n['stl']), os.path.basename(n['model_stl'])))
                m.update(stl=src, stl_sha256=of(src)['sha256'], model=of(src)['geom_hash'], orient=n['orient'], placed_from=n['stl'] if src != n['stl'] else None)
                break
            except Exception: continue                  # the STL is gone or unreadable: no note
    return meshes

def _before_save(proj, files):
    """Ask the gate's guard about the meshes of the archive that is about to be written. None when proj is neither in a
    gated project nor above a ledger; else {"meshes", "stamps"} for record_writes(). Only a GuardRefusal gets out of
    here (the guard's own, or the one of this file when the guard cannot be loaded in enforce mode): any other trouble
    is one printed line and the save goes on."""
    gate, ledger = _roots(proj)
    if not gate and not ledger: return None
    meshes, result = None, {}
    try: meshes = project_meshes(files)
    except Exception as e: print('gate: the meshes of %s could not be read (%s: %s)' % (os.path.basename(proj), type(e).__name__, e))
    if gate:
        guard = None
        try: guard = _tool('gate_guard', os.path.join(gate, 'gate', 'tools', 'gate_guard.py'), os.path.join(_HERE, '..', 'gate', 'gate_guard.py'))
        except BaseException as e:                      # the guard cannot be loaded: a broken or half-copied gate/tools
            if isinstance(e, KeyboardInterrupt): raise
            why = 'gate/tools/gate_guard.py cannot be loaded (%s: %s)' % (type(e).__name__, e)
        else: why = 'gate/project.json is there but gate_guard.py is not (gate/tools)'
        if guard is None:
            if _enforce_by_text(gate):
                raise GuardRefusal('gate: %s and gate/project.json says "mode": "enforce": nothing was written. What was not checked has not '
                                   'passed; repair the gate tools (setup_project.py --update-tools)' % why)
            print('gate: %s: this save is not checked' % why)
            return {'meshes': meshes, 'stamps': None}
        try:
            here = os.path.realpath(proj)
            asked = [n for n in _SETTING_NOTES if n.get('project') in (None, here)]
            try: knows = 'files' in inspect.signature(guard.check).parameters
            except (TypeError, ValueError): knows = False
            if knows: result = guard.check(proj, meshes, gate, files=files, asked=asked) or {}
            else:
                print('gate: gate/tools/gate_guard.py is older than this bambu3mf.py: the fit settings of this save are NOT compared (setup_project.py --update-tools)')
                result = guard.check(proj, meshes, gate) or {}
        except Exception as e:
            if type(e).__name__ == 'GuardRefusal': raise
            print('gate guard: %s: %s (the save goes on)' % (type(e).__name__, e))
    return {'meshes': meshes, 'stamps': result.get('stamps') if isinstance(result, dict) else None}

def record_writes(proj, meshes, stamps=None, tool=None):
    """The one place a .3mf writer tells the print ledger what it wrote: ledger.record_writes(proj, meshes, stamps, tool)
    appends one `write` event per object whose mesh changed to bambulab/ledger/events.jsonl. save() calls it after every
    write; a script that writes a .3mf by other means calls it with project_meshes(files). Does nothing when no
    bambulab/ledger/ folder lies above proj. Never raises."""
    try:
        if meshes is None or not _roots(proj)[1]: return []
        ledger = _tool('ledger', os.path.join(_HERE, 'ledger.py'), os.path.join(_HERE, '..', 'ledger', 'ledger.py'))
        if ledger is None: return []
        return ledger.record_writes(proj, meshes, stamps=stamps, tool=tool) or []
    except NotImplementedError: return []
    except Exception as e:
        print('ledger: the write was not recorded (%s: %s)' % (type(e).__name__, e)); return []

def backup(proj, keep=10):
    """Copy proj into .backups/ next to it, timestamped, keeping the newest `keep`. Call it before a build overwrites hand work."""
    if not os.path.exists(proj): return None
    d = os.path.join(os.path.dirname(os.path.abspath(proj)), '.backups'); os.makedirs(d, exist_ok=True); base = os.path.basename(proj)
    dst = os.path.join(d, time.strftime('%Y%m%d-%H%M%S-') + base); shutil.copy2(proj, dst)
    for old in sorted(f for f in os.listdir(d) if f.endswith('-' + base))[:-keep]: os.remove(os.path.join(d, old))
    return dst

def read_stl(p):
    """Binary STL -> (vertices, triangles). Weld on exact float32 values: rounding would merge distinct vertices.
    Coordinates under 1e-9 mm count as 0: on the seam of a revolved face one side comes out as y = 0 and the other as
    r * sin(2 pi) = 1e-15, two vertices on one spot, and the seam stayed open in Bambu ("30 open edges")."""
    with open(p, 'rb') as f: d = f.read()
    n = struct.unpack('<I', d[80:84])[0]; vid = {}; V = []; F = []
    for i in range(n):
        o = 84 + i * 50 + 12; t = []
        for k in range(3):
            q = tuple(0.0 if abs(c) < 1e-9 else c for c in struct.unpack('<3f', d[o + 12 * k:o + 12 * k + 12]))
            if q not in vid: vid[q] = len(V); V.append(q)
            t.append(vid[q])
        if len(set(t)) == 3: F.append(t)
    return V, F

def open_edges(F):
    """Edges not shared by exactly two triangles in opposite directions: what Bambu Studio reports as open edges."""
    E = {}
    for a, b, c in F:
        for e in ((a, b), (b, c), (c, a)): E[e] = E.get(e, 0) + 1
    return sum(1 for (u, v), n in E.items() if n != 1 or E.get((v, u)) != 1)

def weld(V, F, tol=1e-7):
    """Merge vertices on the same spot (within tol mm); the first one keeps its coordinates. Triangles that collapse are dropped."""
    vid, W, m = {}, [], []
    for v in V:
        k = tuple(round(c / tol) for c in v)
        if k not in vid: vid[k] = len(W); W.append(v)
        m.append(vid[k])
    return W, [t for t in ([m[i] for i in f] for f in F) if len(set(t)) == 3]

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
            note_setting(k, v, name, 'set_settings', proj)
        cfg = cfg.replace('<object id="%s">' % oid + body, '<object id="%s">' % oid + nh + body[len(head):]); hit.append(name)
    if hit:
        files['Metadata/model_settings.config'] = cfg.encode(); save(proj, files, infos, zipfile.ZIP_STORED)
    return hit

LAST_UPDATE = {}     # what the last update_meshes() saw: {"changed", "no_object": [STL files], "no_plate": [object names]}

def update_meshes(proj, stl_dir, plate_prefix='', sources=None):
    """Swap changed meshes for objects on the matching plates whose name matches an STL in stl_dir (same name, or the part name before ' - ').
    Position, rotation, per-object settings and pauses are kept: the new mesh keeps the old bbox centre (xy) and bed contact (z).
    The STL must be in the frame of the mesh it replaces. build() bakes the bed orientation ('+y', '-y', ...) into the vertices,
    so a part built with one needs its STL turned the same way first (ORIENT[face]); an STL straight from CAD is refused when
    its bbox is the old one with the axes swapped (six parts once stood on the wrong face after a raw swap).
    sources: {object or part name: the model-frame STL} when stl_dir holds turned copies, so the gate and the ledger tie the mesh
    to the exported file (a quarter-turned copy is found without it, a free angle is not).
    Two things are said, not skipped: an STL of stl_dir that matches no object of the project, and an object that is on no
    plate (its mesh is never swapped: it is not on any matching plate). Both are also in LAST_UPDATE."""
    key = lambda n: n.split(' - ')[0].replace('.stl', '')
    stls = {key(f): os.path.join(stl_dir, f) for f in os.listdir(stl_dir) if f.endswith('.stl')}
    infos, files = load(proj); cfg = files['Metadata/model_settings.config'].decode(); model = files['3D/3dmodel.model'].decode(); changed = []
    no_object, no_plate = [], []
    try:                                                # the report never stands in the way of the swap
        everything = [(oid, re.search(r'name" value="([^"]*)', body.split('<part')[0])) for oid, body in re.findall(r'<object id="(\d+)">(.*?)</object>', cfg, re.S)]
        everything = [(oid, m.group(1)) for oid, m in everything if m]
        placed = set(re.findall(r'object_id" value="(\d+)"', ''.join(plates(cfg, ''))))
        no_object = sorted(os.path.basename(stls[k]) for k in set(stls) - {key(n) for _, n in everything})
        no_plate = [n for oid, n in everything if oid not in placed]
    except Exception: pass
    if no_object: print('no object in the project for %d STL(s): %s' % (len(no_object), ', '.join(no_object)))
    for n in no_plate: print('on no plate: %s%s' % (n, ' (its STL was NOT swapped in: put the object on a plate first)' if key(n) in stls else ''))
    LAST_UPDATE.clear(); LAST_UPDATE.update(changed=changed, no_object=no_object, no_plate=no_plate)
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
        osz, nsz = [ohi[i] - olo[i] for i in range(3)], [nhi[i] - nlo[i] for i in range(3)]
        if max(abs(osz[i] - nsz[i]) for i in range(3)) > 3.0 and max(abs(p - q) for p, q in zip(sorted(osz), sorted(nsz))) < 3.0:
            raise SystemExit('%s: the STL is not in the frame of the mesh in the project (bbox %s, project %s): orient it for the bed first'
                             % (name, ' x '.join('%.1f' % x for x in nsz), ' x '.join('%.1f' % x for x in osz)))
        # unchanged = same triangle count, same bbox size, volume within 0.001 mm³ (absolute: a relative check once skipped a 10 mm³ change)
        # ... and the same vertices relative to the bbox corner: a hole moved inside the part keeps count, bbox and volume
        # (4 lap screws once moved 1.2 mm in two parts and the swap said "nothing changed")
        same_pts = lambda: sorted((round(x - olo[0], 2), round(y - olo[1], 2), round(z - olo[2], 2)) for x, y, z in set(old)) == \
            sorted((round(x - nlo[0], 2), round(y - nlo[1], 2), round(z - nlo[2], 2)) for x, y, z in set(V))
        if len(oldF) == len(F) and abs(volume(old, oldF) - volume(V, F)) < 1e-3 and all(abs((ohi[i] - olo[i]) - (nhi[i] - nlo[i])) < 1e-3 for i in range(3)) and same_pts(): continue
        oc = [(olo[i] + ohi[i]) / 2 for i in range(2)] + [olo[2]]; nc = [(nlo[i] + nhi[i]) / 2 for i in range(2)] + [nlo[2]]
        off = [oc[i] - nc[i] for i in range(3)]
        files[path] = (om[:blk.start(2)] + mesh_xml([(x + off[0], y + off[1], z + off[2]) for x, y, z in V], F) + om[blk.end(2):]).encode()
        cfg = cfg.replace(body, re.sub(r'face_count="\d+"', 'face_count="%d"' % len(F), body)); changed.append(name)
        note_stl(name, src, False, model_stl=(sources or {}).get(name) or (sources or {}).get(key(name)), tool='update_meshes')
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
    note_stl(o[0], os.path.join(stl_dir, f), flip, tool='prep_object')
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

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'filament_keys.json')) as _f: FILAMENT_KEYS = json.load(_f)   # key -> values per filament

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
        note_setting(k, v, 'global', 'set_project')
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
        note_setting(k, v, 'global', 'set_filament')
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

def new_project(template, proj, filament=None, colours=None, settings=()):
    """Copy a template to proj, optionally down to one filament in the given colours. Then call build(proj, ...).
    When proj exists, the template's settings replace the ones it had. In a gated project the guard compares the fit
    settings of the two: name in `settings` the ones this call is meant to replace ('xy_hole_compensation', ...), or
    keep the template in step with the project."""
    for k in settings or (): note_setting(k, None, 'global', 'new_project', proj)
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
            for k, v in {**settings, **extra.get(name, {}), **per}.items(): note_setting(k, v, name, 'build', proj)
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
