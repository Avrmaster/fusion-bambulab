"""Project-stage probe: what the slicer really supports, per object, read from the G-code of a sliced COPY of a registered
.3mf. The slicer is the judge of supports: a part that passes every geometry check can still get a support tree round
its whole rim (one pulley went from about 150 to 2800 mm of support line on a project with small-overhang supports on).

    slice_project(root, entry, plates)   copy the .3mf into gate/run/slice/<key>.work/, run the Bambu Studio CLI on the
                                         copy (`--slice N`, never on the file itself, safe while the GUI is open), read
                                         every plate_<n>.gcode, delete the G-code, store the record
    record_path(root, key)               gate/run/slice/<key>.json, the record (state of the gate, not of the owner)
    baseline_path(root)                  gate/owner/support_baseline.json, the baseline per object NAME of each project;
                                         only `gate.py owner baseline` writes it, from a record, never from typed numbers
    facts_for(root, key, objects)        what the judge print.slice_support_growth reads (the "slice" block of the
                                         project_consistency facts): each object of the saved project with its sliced
                                         numbers, when they were sliced from the same mesh, and its baseline

How support is attributed to an object. Bambu Studio writes "; OBJECT_ID: <label>" before the moves of each object in a
layer and lists the labels of the plate in the header ("; model label id: a,b,c", with "; object max height: ..." in the
same order); the order is the order of the plate's objects in the CLI's result.json. The mapping is accepted only when
every height of the header agrees with that object's height in result.json within one layer and 0.05 mm (or when the
plate holds one object and one label). Support moves are the extruding G1/G2/G3 moves under "; FEATURE: Support" and
"; FEATURE: Support interface"; their E values are summed (mm of filament, the number the older plate check used) with
the lowest and highest Z. Support printed under no label (a plate with more than one object) cannot be attributed and is
said so."""
import glob, hashlib, json, math, os, re, shutil, subprocess, sys, zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import gate_common
except ImportError:                                  # run from another folder: tools/gate (or gate/tools) is one folder up
    sys.path.insert(0, os.path.dirname(_HERE))
    import gate_common
from gate_common import GateError

RECORD_V = 1
HEIGHT_TOL = 0.05         # mm on top of one layer: how far the header's object height may be from result.json's bbox height
CELL = 1.0                # mm: support is kept per cell of this size in X-Y with its highest Z (where it stands, for print.overhang_angle)
CELLS_MAX = 20000         # cells kept per object; more are dropped and the row says "cells_cut"
SAVE_TIMEOUT = 240        # s per plate when a slice runs inside a save (gate_slice.before_write): a save never waits for half an hour
_E = re.compile(r'E(-?[\d.]+)')
_X = re.compile(r'X(-?[\d.]+)')
_Y = re.compile(r'Y(-?[\d.]+)')
_WALLS = ('Outer wall', 'Inner wall', 'Overhang wall')
# the process settings that change where the slicer puts support: a slice made under other values of them is not this one's
SUPPORT_SETTING = re.compile(r'^(enable_support|support_|tree_support|raft_|enforce_support|bridge_no_support|max_bridge_length|independent_support'
                             r'|layer_height$|initial_layer_print_height$|print_sequence$|overhang_|different_settings_to_system$)')


# ---- reading the G-code ------------------------------------------------------------------------------------------------

def parse_gcode(lines):
    """Support per label of one sliced plate. `lines`: an iterable of G-code lines (an open file).
    Returns {"studio": version text or None, "labels": [int] or None, "heights": [float] or None, "layer_height": float or None,
             "by_label": {label: {"support_e", "interface_e", "low_z", "top_z"}}, "unlabelled": {...} or None}.
    Numbers are rounded to 0.1 mm of filament and 0.01 mm of height."""
    z, feature, label, x, y = 0.0, None, None, None, None
    out = {'studio': None, 'labels': None, 'heights': None, 'layer_height': None, 'by_label': {}, 'unlabelled': None}
    acc, walls, cells = {}, {}, {}
    for line in lines:
        if line.startswith(';'):
            if line.startswith('; Z_HEIGHT:'):
                try:
                    z = float(line.split(':', 1)[1])
                except ValueError:
                    pass
            elif line.startswith('; FEATURE:'):
                feature = line.split(':', 1)[1].strip()
            elif line.startswith('; OBJECT_ID:'):
                try:
                    label = int(line.split(':', 1)[1])
                except ValueError:
                    label = None
            elif line.startswith('; CHANGE_LAYER') or line.startswith('; WIPE_TOWER_START'):
                label = None
            elif line.startswith('; model label id:'):
                try:
                    out['labels'] = [int(x) for x in line.split(':', 1)[1].split(',') if x.strip()]
                except ValueError:
                    out['labels'] = None
            elif line.startswith('; object max height:'):
                try:
                    out['heights'] = [float(x) for x in line.split(':', 1)[1].split(',') if x.strip()]
                except ValueError:
                    out['heights'] = None
            elif out['studio'] is None and line.startswith('; BambuStudio '):
                out['studio'] = line[2:].strip()
            elif out['layer_height'] is None and line.startswith('; layer_height = '):
                try:
                    out['layer_height'] = float(line.split('=', 1)[1])
                except ValueError:
                    pass
            continue
        if line[:3] not in ('G0 ', 'G1 ', 'G2 ', 'G3 '):
            continue
        code = line.split(';', 1)[0]
        px, py = x, y
        mx, my = _X.search(code), _Y.search(code)
        if mx:
            x = float(mx.group(1))
        if my:
            y = float(my.group(1))
        if feature is None or line[:3] == 'G0 ':
            continue
        m = _E.search(code)
        if not m:
            continue
        e = float(m.group(1))
        if e <= 0:
            continue
        if not feature.startswith('Support'):
            if feature in _WALLS and x is not None and y is not None:
                w = walls.setdefault(label, [x, y])
                w[0], w[1] = min(w[0], x, px if px is not None else x), min(w[1], y, py if py is not None else y)
            continue
        r = acc.setdefault(label, {'support_e': 0.0, 'interface_e': 0.0, 'low_z': None, 'top_z': None})
        r['interface_e' if 'interface' in feature else 'support_e'] += e
        r['low_z'] = z if r['low_z'] is None else min(r['low_z'], z)
        r['top_z'] = z if r['top_z'] is None else max(r['top_z'], z)
        if x is not None and y is not None:
            c = cells.setdefault(label, {})
            ax, ay = (px, py) if px is not None and py is not None else (x, y)
            n = max(1, int(math.ceil(math.hypot(x - ax, y - ay) / CELL)))
            for i in range(n + 1):
                k = (int(math.floor((ax + (x - ax) * i / n) / CELL)), int(math.floor((ay + (y - ay) * i / n) / CELL)))
                if c.get(k, -1.0) < z:
                    c[k] = z
    for k, r in acc.items():
        row = {'support_e': round(r['support_e'] + r['interface_e'], 1), 'interface_e': round(r['interface_e'], 1),
               'low_z': round(r['low_z'], 2) if r['low_z'] is not None else None, 'top_z': round(r['top_z'], 2) if r['top_z'] is not None else None}
        row.update(_cells=cells.get(k) or {}, _wall=walls.get(k))
        if k is None:
            out['unlabelled'] = row
        else:
            out['by_label'][k] = row
    for k, w in walls.items():
        if k is not None and k not in out['by_label']:
            out['by_label'][k] = {'support_e': 0.0, 'interface_e': 0.0, 'low_z': None, 'top_z': None, '_cells': {}, '_wall': w}
    return out


def _cells_of(cells, wall):
    """(after the mapping) {"origin": [x, y] the low corner of the object's walls in the G-code (line centres) or None, "cells": [[x, y, top z]]
    the support cells relative to that corner (cell centres, mm), or None without walls, "cells_cut": bool}."""
    if not wall:
        return {'origin': None, 'cells': None, 'cells_cut': False}
    rows = sorted(cells.items(), key=lambda kv: -kv[1])
    cut = len(rows) > CELLS_MAX
    return {'origin': [round(wall[0], 2), round(wall[1], 2)], 'cells_cut': cut,
            'cells': sorted([round((kx + 0.5) * CELL - wall[0], 1), round((ky + 0.5) * CELL - wall[1], 1), round(top, 2)] for (kx, ky), top in rows[:CELLS_MAX])}


def map_objects(plate_result, parsed, layer_height=None, printable=None):
    """The objects of one sliced plate with their support: ([{"name", "label", "height", "support_e", "interface_e",
    "low_z", "top_z", "printed"}], mapping text, problem or None). plate_result: one entry of result.json
    "sliced_plates" (its "objects" [{"name", "bbox": {"height"}}]). printable: the names of the plate's objects that
    print (the slicer lists the switched-off ones too, and prints nothing of them); None = every one. Without a sound
    mapping every printed object has support_e None and the problem says why; support printed under no label is added
    to the only printed object of a plate."""
    every = [o for o in plate_result.get('objects') or [] if isinstance(o, dict)]
    off = [{'name': o.get('name'), 'label': None, 'height': round(float((o.get('bbox') or {}).get('height') or 0.0), 3), 'support_e': None,
            'interface_e': None, 'low_z': None, 'top_z': None, 'printed': False} for o in every if printable is not None and o.get('name') not in printable]
    objs = [o for o in every if printable is None or o.get('name') in printable]
    labels, heights, by = parsed.get('labels'), parsed.get('heights'), parsed.get('by_label') or {}
    lh = layer_height or parsed.get('layer_height') or 0.0
    rows = [{'name': o.get('name'), 'label': None, 'height': round(float((o.get('bbox') or {}).get('height') or 0.0), 3),
             'support_e': None, 'interface_e': None, 'low_z': None, 'top_z': None, 'printed': True, 'origin': None, 'cells': None, 'cells_cut': False} for o in objs]
    rows, problem = _mapped(rows, labels, heights, by, lh, parsed.get('unlabelled'))
    return rows[0] + off, rows[1], problem


def _mapped(rows, labels, heights, by, lh, loose):
    """map_objects() for the printed objects: ((rows, mapping text), problem)."""
    problem, how = None, None
    if not rows:
        return (rows, None), 'the slicer lists no printed object on this plate'
    objs = rows
    if labels and len(labels) == len(objs):
        off = [abs(h - r['height']) for h, r in zip(heights or [], rows)] if heights and len(heights) == len(rows) else None
        if off is None:
            problem = 'the G-code header gives no object heights to check the label order against'
        elif max(off) > lh + HEIGHT_TOL:
            worst = max(range(len(off)), key=lambda i: off[i])
            problem = ('the label order of the G-code does not fit the objects: %s is %.2f high, its label %d reaches %.2f'
                       % (rows[worst]['name'], rows[worst]['height'], labels[worst], heights[worst]))
        else:
            for r, lab in zip(rows, labels):
                r['label'] = lab
            how = 'labels of the G-code header in the order of the plate, every height agrees'
    elif len(objs) == 1 and (not labels or len(labels) == 1) and len(by) <= 1:
        rows[0]['label'] = (labels or list(by) or [None])[0]
        how = 'the only object of the plate'
    else:
        problem = 'the G-code names %s label(s) for %d object(s)' % (len(labels) if labels else 'no', len(objs))
    if problem:
        return (rows, None), problem
    known = {r['label'] for r in rows}
    stray = [k for k in by if k not in known]
    if stray:
        return (rows, None), 'support printed under label(s) %s that no object of the plate has' % ', '.join(str(k) for k in sorted(stray))
    raw = {}
    for r in rows:
        got = by.get(r['label']) or {'support_e': 0.0, 'interface_e': 0.0, 'low_z': None, 'top_z': None}
        r.update(support_e=got['support_e'], interface_e=got['interface_e'], low_z=got['low_z'], top_z=got['top_z'])
        raw[id(r)] = (dict(got.get('_cells') or {}), got.get('_wall'))
    if loose and loose.get('support_e'):
        if len(rows) == 1:
            r = rows[0]
            r['support_e'] = round(r['support_e'] + loose['support_e'], 1)
            r['interface_e'] = round((r['interface_e'] or 0.0) + loose['interface_e'], 1)
            r['low_z'] = loose['low_z'] if r['low_z'] is None else min(r['low_z'], loose['low_z'])
            r['top_z'] = loose['top_z'] if r['top_z'] is None else max(r['top_z'], loose['top_z'])
            c, w = raw[id(r)]
            for k, top in (loose.get('_cells') or {}).items():
                c[k] = max(c.get(k, -1.0), top)
        else:
            for r in rows:
                r.update(support_e=None, interface_e=None, low_z=None, top_z=None, origin=None, cells=None, cells_cut=False)
            return (rows, None), '%.1f mm of support printed under no object label on a plate of %d printed objects' % (loose['support_e'], len(rows))
    for r in rows:
        r.update(_cells_of(*raw[id(r)]))
    return (rows, how), None


# ---- files ---------------------------------------------------------------------------------------------------------------

def record_path(root, key):
    return gate_common.gate_path(root, 'run', 'slice', '%s.json' % gate_common.slug(str(key)))


def baseline_path(root):
    return gate_common.gate_path(root, 'owner', 'support_baseline.json')


def load_record(root, key):
    data = gate_common.read_json(record_path(root, key), None)
    return data if isinstance(data, dict) and data.get('v') == RECORD_V else None


def load_baseline(root):
    """(the baseline file's "projects" {key: {object name: entry}}, problem or None). A missing file is an empty baseline;
    a file that is not an object with "projects" is a problem (nothing in it counts)."""
    missing = object()
    data = gate_common.read_json(baseline_path(root), missing)      # no isfile test: inside `owner bundle` a staged write is read
    if data is missing:
        return {}, None
    if not isinstance(data, dict) or not isinstance(data.get('projects'), dict):
        return {}, 'gate/owner/support_baseline.json must be an object with "projects": {key: {object name: entry}}'
    return data['projects'], None


def studio_exe():
    """The Bambu Studio executable (bambu3mf.studio_path: BAMBU_STUDIO overrides the search), or None."""
    try:
        return gate_common.sibling('bambu3mf').studio_path()
    except Exception:
        return os.environ.get('BAMBU_STUDIO') or None


def _members(source):
    """{member: bytes} of the members slice_keys reads, from a path or from an archive {member: bytes}."""
    want = lambda n: n in ('3D/3dmodel.model', 'Metadata/model_settings.config', 'Metadata/project_settings.config') or n.startswith('3D/Objects/')
    if isinstance(source, dict):
        return {n: v for n, v in source.items() if want(n)}
    try:
        with zipfile.ZipFile(source) as z:
            return {n: z.read(n) for n in z.namelist() if want(n)}
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as e:
        raise GateError('%s cannot be read as a .3mf project (%s: %s)' % (source, type(e).__name__, e))


_KEYS = {}                 # (path, mtime_ns, size) -> slice_keys() of that file state
_OBJ_BLOCK = re.compile(r'<object\s+id="([^"]*)"[^>]*>(.*?)</object>', re.S)


def slice_keys(source):
    """{(plate index, object name): (pose, slice key)} of a .3mf (a path or an archive {member: bytes}). The pose is
    meshid.geom_hash of the stored vertices (the hash of the guard and the ledger). The slice key adds what the pose
    leaves out and the slicer does not: the turn of the object's build item (a hand turn in the slicer lives there, not
    in the vertices), the object's whole block of Metadata/model_settings.config (its per-object settings, its parts and
    modifiers) and the process settings that move support (SUPPORT_SETTING). A slice is this object's slice only while
    its key is the same: an object turned on its plate, or a support setting changed, is sliced again."""
    key = None
    if not isinstance(source, dict):
        try:
            st = os.stat(source)
            key = (os.path.abspath(source), st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
        if key is not None and key in _KEYS:
            return dict(_KEYS[key])
    meshid = gate_common.sibling('meshid')
    from probes import project_bed
    files = _members(source)
    items = project_bed._items((files.get('3D/3dmodel.model') or b'').decode('utf-8', 'replace'))
    blocks = {oid: body for oid, body in _OBJ_BLOCK.findall((files.get('Metadata/model_settings.config') or b'').decode('utf-8', 'replace'))}
    try:
        ps = json.loads((files.get('Metadata/project_settings.config') or b'{}').decode('utf-8', 'replace'))
    except ValueError:
        ps = None
    glob_ = sorted((k, v) for k, v in ps.items() if SUPPORT_SETTING.match(k)) if isinstance(ps, dict) else 'unreadable'
    out = {}
    for m in meshid.project_meshes(files):
        pose = meshid.geom_hash(m['vertices'], len(m['triangles']))
        lin = items.get(str(m.get('id')), (project_bed._IDENTITY, None))[0]
        text = json.dumps([pose, [round(c, 6) + 0.0 for c in lin], blocks.get(str(m.get('id'))), glob_], sort_keys=True, default=str)
        out[(m.get('plate_index'), m['object'])] = (pose, hashlib.sha1(text.encode('utf-8')).hexdigest()[:16])
    if key is not None:
        if len(_KEYS) > 16:
            _KEYS.clear()
        _KEYS[key] = dict(out)                       # verify reads each project for its slice keys more than once
    return out


def _poses(path):
    """[(plate index, object name, pose hash, printable, slice key)] of a .3mf, from meshid (the pose hashes of the guard)."""
    meshid = gate_common.sibling('meshid')
    keys = slice_keys(path)
    return [(m.get('plate_index'), m['object'], meshid.geom_hash(m['vertices'], len(m['triangles'])), bool(m.get('printable', True)),
             (keys.get((m.get('plate_index'), m['object'])) or (None, None))[1])
            for m in meshid.read_project(path)]


def plates_of(path):
    """{plate index: plate name} of a .3mf (plates that hold no object are left out)."""
    out = {}
    for m in gate_common.sibling('meshid').read_project(path):
        if m.get('plate_index') is not None:
            out.setdefault(m['plate_index'], m.get('plate') or 'plate %d' % m['plate_index'])
    return out


def changed_plates(root, entry, source=None):
    """Plate indexes of the project whose printed objects (name and slice key: the mesh, its turn on the plate and the
    settings that move support) differ from the last record: the plates to slice. source: an archive {member: bytes}
    or another file to read instead of the registered one (a save about to be written)."""
    path = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, entry['file'])
    rec = load_record(root, entry.get('key')) or {}
    now = {}
    if isinstance(source, dict):
        meshid = gate_common.sibling('meshid')
        keys = slice_keys(source)
        listed = [(m.get('plate_index'), m['object'], bool(m.get('printable', True))) for m in meshid.project_meshes(source)]
        rows = [(i, n, p, (keys.get((i, n)) or (None, None))[1]) for i, n, p in listed]
    else:
        rows = [(i, n, p, k) for i, n, _pose, p, k in _poses(source or path)]
    for index, name, printable, skey in rows:
        if index is not None and printable:
            now.setdefault(index, set()).add((name, skey))
    out = []
    for index, objs in sorted(now.items()):
        old = (rec.get('plates') or {}).get(str(index)) or {}
        if old.get('error') or {(o.get('name'), o.get('skey')) for o in old.get('objects') or [] if o.get('printed') is not False} != objs:
            out.append(index)
    return out


# ---- slicing -------------------------------------------------------------------------------------------------------------

def run_cli(exe, copy, out_dir, plate, timeout):
    """One CLI run on the copy: (result.json content or {}, the tail of its output). Raises GateError on a timeout."""
    try:
        r = subprocess.run([exe, '--slice', str(plate), '--outputdir', out_dir, copy], cwd=out_dir, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GateError('the slicer did not finish plate %s within %d s' % (plate, timeout))
    except (OSError, ValueError, UnicodeError) as e:
        raise GateError('the slicer could not be started (%s: %s)' % (type(e).__name__, e))
    res = os.path.join(out_dir, 'result.json')
    data = gate_common.read_json(res, {}) if os.path.isfile(res) else {}
    tail = '\n'.join(l for l in (r.stdout + r.stderr).splitlines() if '[error]' not in l or 'conflict' in l.lower())[-800:]
    if r.returncode and not data:
        data = {'return_code': r.returncode}
    return data if isinstance(data, dict) else {}, tail


def _locked_update(root, key, fn, wait_s=30.0):
    """Re-read the record of `key`, apply fn(record) and write it, under gate/run/slice/<key>.lock: two slices of one
    project (a background `gate.py slice` and a save) each keep the plates they sliced. A lock older than 10 minutes is
    left over by a process that died and is taken over."""
    import time
    lock = gate_common.gate_path(root, 'run', 'slice', '%s.lock' % gate_common.slug(str(key)))
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 600:
                    os.remove(lock)
                    continue
            except OSError:
                continue
            if time.time() - t0 > wait_s:
                raise GateError('the slice record of %s is locked by another slice (%s): try again when it is done' % (key, gate_common.rel(root, lock)))
            time.sleep(0.05)
    try:
        rec = load_record(root, key) or {'v': RECORD_V, 'project': key, 'plates': {}}
        fn(rec)
        gate_common.write_json(record_path(root, key), rec)
        return rec
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def slice_project(root, entry, plates=None, exe=None, timeout=1800, keep=False, cli=None, source=None):
    """Slice plates of one registered project on a copy and store the record. plates: [indexes], None = every plate
    that holds an object. cli: a stand-in for run_cli (tests). source: another file to slice for this project (the
    archive a save is about to write); the record stays the project's. Each run works in a folder of its own and
    merges its plates into the record under a lock (_locked_update). A plate whose run fails or times out is a plate
    row with "error", never a number. Returns the record (also written to record_path)."""
    key = entry.get('key')
    path = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, entry['file'])
    registered = path
    path = source or path
    if not os.path.isfile(path):
        raise GateError('%s does not exist: nothing to slice' % gate_common.rel(root, path))
    exe = exe or studio_exe()
    if cli is None and not exe:
        raise GateError('Bambu Studio is not found: set BAMBU_STUDIO to its executable. Nothing was sliced, and supports stay NOT_RUN')
    cli = cli or run_cli
    work = gate_common.gate_path(root, 'run', 'slice', '%s.%d.work' % (gate_common.slug(str(key)), os.getpid()))
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    copy = os.path.join(work, 'in.3mf')
    shutil.copyfile(path, copy)                       # the slicer only ever sees the copy
    sha = gate_common.sha256_file(copy)
    names = plates_of(copy)
    want = sorted(names) if plates is None else [p for p in plates if p in names]
    poses, printable = {}, {}
    for index, name, pose, on, skey in _poses(copy):
        poses.setdefault((index, name), []).append((pose, skey))
        printable[(index, name)] = printable.get((index, name), False) or on
    done, studio = {}, [None]
    try:
        groups = [[0]] if plates is None and len(want) > 1 else [[p] for p in want]
        for group in groups:
            out_dir = os.path.join(work, 'plate_%s' % group[0])
            os.makedirs(out_dir, exist_ok=True)
            try:
                data, tail = cli(exe, copy, out_dir, group[0], timeout)
            except GateError as e:                    # a timeout or a CLI that cannot start: these plates are not sliced
                data, tail = {'return_code': -1, 'error_string': str(e)}, ''
            failed = data.get('return_code', 0) or not data.get('sliced_plates')
            sliced = {int(p.get('id')): p for p in data.get('sliced_plates') or [] if isinstance(p, dict) and p.get('id') is not None}
            for index in (want if group == [0] else group):
                row = {'name': names.get(index), 'time': gate_common.now(), 'file_sha256': sha, 'error': None, 'mapping': None, 'objects': [], 'cells_mm': CELL}
                gcode = os.path.join(out_dir, 'plate_%d.gcode' % index)
                if failed or index not in sliced or not os.path.isfile(gcode):
                    row['error'] = 'the slicer gave no G-code for this plate (%s)%s' % (
                        data.get('error_string') or 'return code %s' % data.get('return_code'), (': ' + tail.splitlines()[-1]) if tail.strip() else '')
                else:
                    with open(gcode, errors='ignore') as f:
                        parsed = parse_gcode(f)
                    studio[0] = parsed.get('studio') or studio[0]
                    printing = {n for (i, n), ps in printable.items() if i == index and ps}
                    objs, how, problem = map_objects(sliced[index], parsed, data.get('layer_height'), printing)
                    row['mapping'], row['error'] = how, problem
                    for o in objs:
                        got = poses.get((index, o['name'])) or []
                        o['pose'], o['skey'] = got[0] if len(got) == 1 else (None, None)
                    row['objects'] = objs
                done[str(index)] = row
            for g in glob.glob(os.path.join(out_dir, '*.gcode')):
                if not keep:
                    os.remove(g)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)

    def merge(rec):
        rec.update(v=RECORD_V, project=key, file=gate_common.rel(root, registered), time=gate_common.now())
        rec.setdefault('plates', {}).update(done)
        rec['studio'] = studio[0] or rec.get('studio')
    return _locked_update(root, key, merge)


# ---- the facts the judge reads -------------------------------------------------------------------------------------------

def facts_for(root, key, objects, source=None):
    """The "slice" block of the project_consistency facts of one project. objects: its object rows ({"name", "plate",
    "plate_index", "printable", "pose", "part"}). Returns {"record": path or None, "time", "studio", "baseline_file",
    "baseline_problem", "plates": {index: {"error", "mapping", "time"}}, "objects": [{"name", "part", "plate",
    "plate_index", "printable", "pose", "sliced": {"support_e", "interface_e", "low_z", "top_z", "plate_index", "time",
    "pose", "skey", "origin", "cells", "cells_cut"} or None, "why": text when "sliced" is None, "baseline": {"support_e",
    "top_z", "pose", "date", "quote"} or None, "skey"}]}.
    source: the .3mf (path or archive {member: bytes}) the rows were read from, for their slice keys (slice_keys); None =
    the registered file of `key`. A slice counts for an object only when its slice key is the object's now: the same
    mesh, turned the same way on its plate, under the same support settings."""
    rec = load_record(root, key)
    if source is None:
        entry = next((e for e in (gate_common.load_layout(root).get('projects') or []) if isinstance(e, dict) and e.get('key') == key and e.get('file')), None)
        if entry:
            source = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, entry['file'])
    try:
        keys = slice_keys(source) if source is not None and (isinstance(source, dict) or os.path.isfile(source)) else {}
    except GateError:
        keys = {}
    base, problem = load_baseline(root)
    mine = base.get(key) if isinstance(base.get(key), dict) else {}
    out = {'record': gate_common.rel(root, record_path(root, key)) if rec else None, 'time': (rec or {}).get('time'), 'studio': (rec or {}).get('studio'),
           'baseline_file': gate_common.rel(root, baseline_path(root)), 'baseline_problem': problem, 'plates': {}, 'objects': []}
    sliced = {}
    for index, p in sorted(((rec or {}).get('plates') or {}).items()):
        if not isinstance(p, dict):
            continue
        out['plates'][index] = {'error': p.get('error'), 'mapping': p.get('mapping'), 'time': p.get('time'), 'name': p.get('name')}
        for o in p.get('objects') or []:
            if isinstance(o, dict) and o.get('name'):
                sliced.setdefault(o['name'], []).append(dict(o, plate_index=int(index) if str(index).isdigit() else index, time=p.get('time'), error=p.get('error')))
    for r in objects or []:
        row = {k: r.get(k) for k in ('name', 'part', 'plate', 'plate_index', 'printable', 'pose')}
        row['skey'] = (keys.get((r.get('plate_index'), r.get('name'))) or (None, None))[1]
        b = mine.get(r.get('name')) if isinstance(mine, dict) else None
        row['baseline'] = {k: b.get(k) for k in ('support_e', 'top_z', 'pose', 'date', 'quote')} if isinstance(b, dict) else None
        cands = sliced.get(r.get('name')) or []
        posed = [c for c in cands if c.get('pose') and c['pose'] == r.get('pose')]
        same = [c for c in posed if c.get('skey') and c['skey'] == row['skey']]
        row['sliced'], row['why'] = None, None
        if rec is None:
            row['why'] = 'the project was never sliced by the gate'
        elif same:
            c = same[-1]
            if c.get('error') or c.get('support_e') is None:
                row['why'] = 'the slice of plate %s says no support per object: %s' % (c.get('plate_index'), c.get('error') or 'no number')
            else:
                row['sliced'] = {k: c.get(k) for k in ('support_e', 'interface_e', 'low_z', 'top_z', 'plate_index', 'time', 'pose', 'skey', 'origin', 'cells', 'cells_cut')}
        elif isinstance(((rec or {}).get('plates') or {}).get(str(r.get('plate_index'))), dict) and rec['plates'][str(r.get('plate_index'))].get('error') \
                and not any(o.get('name') == r.get('name') and o.get('pose') == r.get('pose') for o in rec['plates'][str(r.get('plate_index'))].get('objects') or []):
            p = rec['plates'][str(r.get('plate_index'))]
            row['why'] = 'the last slice of plate %s (%s) failed: %s' % (r.get('plate_index'), str(p.get('time') or '')[:16], p['error'])
        elif posed and row['skey'] is None:
            row['why'] = 'the slice key of this object could not be read from the project, so no slice is known to be its own'
        elif posed and not any(c.get('skey') for c in posed):
            row['why'] = 'plate %s was sliced (%s) by a gate that did not record the turn and the settings of each object: slice it again' % (
                posed[-1].get('plate_index'), str(posed[-1].get('time') or '')[:16])
        elif posed:
            row['why'] = 'the object was turned on its plate, or a support setting changed, since plate %s was sliced (%s)' % (
                posed[-1].get('plate_index'), str(posed[-1].get('time') or '')[:16])
        elif cands:
            row['why'] = 'the mesh changed since plate %s was sliced (%s)' % (cands[-1].get('plate_index'), str(cands[-1].get('time') or '')[:16])
        else:
            row['why'] = 'no slice of the gate holds this object yet'
        out['objects'].append(row)
    return out
