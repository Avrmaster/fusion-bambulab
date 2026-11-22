"""Host-side measurement: how each object of a saved .3mf project lies on the bed, measured against the STL it was made
from. Reads files only. No Fusion, no slicer.

The saved project is the truth about orientation: a build script lays a part on a face, and the owner may turn it again
in the slicer (an arrange step, a hand turn). So the bed face is not read from what a script meant but measured:

  1. the object's mesh as placed = the stored vertices (component transforms applied, meshid.project_meshes) moved by
     the linear part of its build item's transform (a turn the slicer keeps outside the vertices);
  2. that mesh is the STL moved as a rigid body: found by the triangles, which both keep in the same order when the
     tools placed the mesh (triangle i of the STL is triangle i of the object), else by the 24 quarter turns of the
     vertex set. Every triangle centre (or every vertex) must land within TOL mm: a proof, not a likeness;
  3. the face on the bed is the direction that motion sends to "down" (0, 0, -1), expressed in the frame of the STL.

A mesh that is not the STL (an older geometry still placed, a free-angle turn of a mesh whose triangles were reordered,
a scaled object) is not measured. For an older geometry the last measurement of the same placed mesh is used when it
is on record in gate/run/bed_faces.json (the mesh as placed is unchanged, so is its face); otherwise the build table is
the fallback (probes.project_consistency.orientations), and when that says nothing the face is unknown and said so."""
import math, os, re, zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import gate_common
except ImportError:                                  # run from another folder: tools/gate (or gate/tools) is one folder up
    import sys
    sys.path.insert(0, os.path.dirname(_HERE))
    import gate_common

TOL = 0.005               # mm: a triangle centre or a vertex of the STL, moved, lies this close to the placed one
ORTHO_TOL = 1e-4          # a build item's linear part this close to a rotation counts as one (no scale, no shear)
MEMORY = ('run', 'bed_faces.json')
_PLACED = {}              # file key -> read_placed()
_STLS = {}                # file key -> (V, F)
_MOTIONS = {}             # (project file key, object id, stl file key) -> solve() result or None
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


CODE_FILES = ('project_bed.py', 'project_consistency.py', 'stl_mesh.py')     # with meshid.py: what a measurement here runs
_CODE = []


def _code():
    """12 hex over the code a bed face measured here depends on: this file, project_consistency.py and stl_mesh.py of
    the probes, and meshid.py (it reads the projects and the STLs). A new version of any of them measures again
    (placed_cache.json): a kept verdict of older code is never taken over. Read once per process."""
    if not _CODE:
        import hashlib
        h = hashlib.sha1()
        paths = [os.path.join(_HERE, n) for n in CODE_FILES]
        try:
            paths.append(os.path.abspath(gate_common.sibling('meshid').__file__))
        except Exception:
            paths.append(None)
        for path in paths:
            try:
                with open(path, 'rb') as f:
                    h.update(hashlib.sha1(f.read()).hexdigest().encode())
            except (OSError, TypeError):
                h.update(b'missing')
        _CODE.append(h.hexdigest()[:12])
    return _CODE[0]


_PENDING = {}             # real path of a .3mf -> {"files": the archive a save is about to write, "key", "token"}
_PENDING_READ = {}        # ("pending", real path, token id) -> read_placed() of that archive, while pending() is active


class pending(object):
    """with pending(path, files, key): every measurement here reads `files` (the archive a save is about to write,
    {member: bytes}) for the project file `path` instead of the file on disk: the guard holds a stamp's print frame and
    the answers it rests on against what will be in the file, not against what was. A file that is no registered
    project is read as one more project (`key`, or its path). Nothing is kept on disk while it is active
    (placed_cache.json, bed_faces.json)."""

    def __init__(self, path, files, key=None):
        self.real, self.entry, self.before = os.path.realpath(path), {'files': files, 'key': key, 'token': object()}, None

    def __enter__(self):
        self.before = _PENDING.get(self.real)
        _PENDING[self.real] = self.entry
        return self

    def __exit__(self, *exc):
        real, before = self.real, self.before
        _PENDING_READ.clear()
        for k in [k for k in _MOTIONS if isinstance(k[0], tuple) and k[0][:1] == ('pending',)]:
            _MOTIONS.pop(k, None)                        # a token's id may come back for another archive
        if before is None:
            _PENDING.pop(real, None)
        else:
            _PENDING[real] = before
        return False


def _file_key(path):
    st = os.stat(path)
    return os.path.abspath(path), st.st_mtime_ns, st.st_size


def _items(model_text):
    """{object id: (linear 3x3 row-major as 9 floats, translation (x, y, z))} of the build items. 3MF writes a
    transform as 12 numbers m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32 and moves a point p to p * M (rows):
    x' = x m00 + y m10 + z m20 + m30. The linear part is returned so that L[i][j] gives x'_i = sum_j L[i][j] x_j."""
    out = {}
    for tag in re.findall(r'<item\s[^>]*>', model_text or ''):
        a = dict(re.findall(r'([\w:]+)="([^"]*)"', tag))
        if 'objectid' not in a or a['objectid'] in out:
            continue
        try:
            t = [float(x) for x in (a.get('transform') or '').split()]
        except ValueError:
            t = []
        if len(t) != 12:
            t = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        lin = (t[0], t[3], t[6], t[1], t[4], t[7], t[2], t[5], t[8])
        out[a['objectid']] = (lin, (t[9], t[10], t[11]))
    return out


def read_placed(path):
    """The objects of a .3mf as they are placed, read once per file state: {"objects": [{"name", "id", "plate",
    "plate_index", "printable", "vertices" (stored form), "triangles", "linear" (9 floats, the build item's turn),
    "move" (its translation), "pose" (meshid.geom_hash of the stored form, the pose hash of the guard and the ledger)}]}.
    `path` may also be the archive itself, {member: bytes} (a save about to be written): it is read, not kept.
    Raises gate_common.GateError when the file cannot be read as a project."""
    want = lambda n: n in ('3D/3dmodel.model', 'Metadata/model_settings.config') or n.startswith('3D/Objects/')
    key = None if isinstance(path, dict) else _file_key(path)
    if key is not None and key in _PLACED:
        return _PLACED[key]
    meshid = gate_common.sibling('meshid')
    if isinstance(path, dict):
        files = {n: v for n, v in path.items() if want(n)}
    else:
        try:
            with zipfile.ZipFile(path) as z:
                files = {n: z.read(n) for n in z.namelist() if want(n)}
        except (OSError, zipfile.BadZipFile, KeyError, ValueError) as e:
            raise gate_common.GateError('%s cannot be read as a .3mf project (%s: %s)' % (path, type(e).__name__, e))
    items = _items((files.get('3D/3dmodel.model') or b'').decode('utf-8', 'replace'))
    objects = []
    for m in meshid.project_meshes(files):
        lin, move = items.get(str(m.get('id')), (_IDENTITY, (0.0, 0.0, 0.0)))
        objects.append({'name': m['object'], 'id': m.get('id'), 'plate': m.get('plate'), 'plate_index': m.get('plate_index'),
                        'printable': bool(m.get('printable', True)), 'vertices': m['vertices'], 'triangles': m['triangles'],
                        'linear': lin, 'move': move, 'pose': meshid.geom_hash(m['vertices'], len(m['triangles']))})
    out = {'objects': objects}
    if key is not None:
        if len(_PLACED) > 8:
            _PLACED.clear()
        _PLACED[key] = out
    return out


def _stl(path):
    key = _file_key(path)
    if key not in _STLS:
        if len(_STLS) > 256:
            _STLS.clear()
        _STLS[key] = gate_common.sibling('meshid').read_stl(path)
    return _STLS[key]


# ---- small linear algebra ----------------------------------------------------------------------------------------------

def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _norm(a): return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else None


def mat_apply(M, p):
    """M (9 floats, row-major) times the point p."""
    return (M[0] * p[0] + M[1] * p[1] + M[2] * p[2], M[3] * p[0] + M[4] * p[1] + M[5] * p[2], M[6] * p[0] + M[7] * p[1] + M[8] * p[2])


def mat_mul(A, B):
    return tuple(sum(A[3 * i + k] * B[3 * k + j] for k in range(3)) for i in range(3) for j in range(3))


def is_rotation(M, tol=ORTHO_TOL):
    """Is M (9 floats) a proper rotation: orthonormal rows, determinant +1?"""
    rows = (M[0:3], M[3:6], M[6:9])
    for i in range(3):
        for j in range(3):
            if abs(_dot(rows[i], rows[j]) - (1.0 if i == j else 0.0)) > tol:
                return False
    return _dot(rows[0], _cross(rows[1], rows[2])) > 0


def _frame(c, p1, p2):
    a = _unit(_sub(p1, c))
    if a is None:
        return None
    b = _sub(p2, c)
    w = (b[0] - _dot(b, a) * a[0], b[1] - _dot(b, a) * a[1], b[2] - _dot(b, a) * a[2])
    w = _unit(w) if _norm(w) > 1e-6 else None
    if w is None:
        return None
    return a, w, _cross(a, w)


def _centres(V, F):
    return [((V[a][0] + V[b][0] + V[c][0]) / 3.0, (V[a][1] + V[b][1] + V[c][1]) / 3.0, (V[a][2] + V[b][2] + V[c][2]) / 3.0) for a, b, c in F]


def _rigid_from_pairs(A, B):
    """The rigid motion (R as 9 floats, t) that sends the point list A onto B, pairs at equal index, from the centroid and
    two well-spread points; None when A is degenerate. Not yet checked on all points."""
    n = float(len(A))
    ca = tuple(math.fsum(p[i] for p in A) / n for i in range(3)); cb = tuple(math.fsum(p[i] for p in B) / n for i in range(3))
    i1 = max(range(len(A)), key=lambda i: _dot(_sub(A[i], ca), _sub(A[i], ca)))
    u = _sub(A[i1], ca)
    i2 = max(range(len(A)), key=lambda i: _dot(_cross(u, _sub(A[i], ca)), _cross(u, _sub(A[i], ca))))
    fa, fb = _frame(ca, A[i1], A[i2]), _frame(cb, B[i1], B[i2])
    if fa is None or fb is None:
        return None
    R = tuple(sum(fb[k][i] * fa[k][j] for k in range(3)) for i in range(3) for j in range(3))
    Rc = mat_apply(R, ca)
    return R, (cb[0] - Rc[0], cb[1] - Rc[1], cb[2] - Rc[2])


def _worst(A, B, R, t, tol):
    """The largest distance of R a + t from its b over all pairs, or None as soon as one is beyond tol."""
    worst = 0.0
    for a, b in zip(A, B):
        p = mat_apply(R, a)
        d = max(abs(p[0] + t[0] - b[0]), abs(p[1] + t[1] - b[1]), abs(p[2] + t[2] - b[2]))
        if d > tol:
            return None
        worst = max(worst, d)
    return worst


def _quarter_turn(stl_V, placed_V, tol):
    """(R, t, worst) when the placed vertex set is the STL's turned by one of the 24 quarter turns and moved, else None."""
    meshid = gate_common.sibling('meshid')
    q = 1.0 / max(tol, 1e-6)
    lo = [min(v[k] for v in placed_V) for k in range(3)]
    mine = {}
    for v in placed_V:
        mine.setdefault((int(round((v[0] - lo[0]) * q)), int(round((v[1] - lo[1]) * q)), int(round((v[2] - lo[2]) * q))), v)
    near = [(a, b, c) for a in (0, -1, 1) for b in (0, -1, 1) for c in (0, -1, 1)]
    for p, s in meshid.QUARTER_TURNS:
        R = tuple(float(s[i]) if j == p[i] else 0.0 for i in range(3) for j in range(3))
        T = [mat_apply(R, v) for v in stl_V]
        tlo = [min(v[k] for v in T) for k in range(3)]
        t = (lo[0] - tlo[0], lo[1] - tlo[1], lo[2] - tlo[2])
        worst, hit = 0.0, set()
        for v in T:
            w = (v[0] + t[0], v[1] + t[1], v[2] + t[2])
            k = (int(round((w[0] - lo[0]) * q)), int(round((w[1] - lo[1]) * q)), int(round((w[2] - lo[2]) * q)))
            got = next((mine[(k[0] + a, k[1] + b, k[2] + c)] for a, b, c in near if (k[0] + a, k[1] + b, k[2] + c) in mine), None)
            if got is None or max(abs(got[i] - w[i]) for i in range(3)) > 2 * tol:
                worst = None
                break
            worst = max(worst, max(abs(got[i] - w[i]) for i in range(3))); hit.add(id(got))
        if worst is not None and len(hit) == len(mine):
            return R, t, worst
    return None


def solve(stl_V, stl_F, placed_V, placed_F, tol=TOL):
    """How the placed mesh is the STL moved: {"R": 9 floats (STL frame -> placed frame), "t", "worst": mm, "method":
    "triangles in order" | "quarter turn"} or None when it is not a rigid copy (another geometry, a mirror, a free turn
    of reordered triangles)."""
    if not stl_F or len(stl_F) != len(placed_F) or len(stl_F) < 4:
        return None
    A, B = _centres(stl_V, stl_F), _centres(placed_V, placed_F)
    got = _rigid_from_pairs(A, B)
    if got is not None and is_rotation(got[0], 1e-6):
        worst = _worst(A, B, got[0], got[1], tol)
        if worst is not None:
            return {'R': got[0], 't': got[1], 'worst': round(worst, 6), 'method': 'triangles in order'}
    got = _quarter_turn(stl_V, placed_V, tol)
    if got is not None:
        return {'R': got[0], 't': got[1], 'worst': round(got[2], 6), 'method': 'quarter turn'}
    return None


def down_in_stl(R, linear):
    """The outward normal, in the STL frame, of the face on the bed: L^T (0, 0, -1) with L = linear R (L a rotation)."""
    L = mat_mul(linear, R)
    return (-L[6], -L[7], -L[8])


def _face_name(d):
    names = {(0, 0, -1): '-z', (0, 0, 1): '+z', (0, 1, 0): '+y', (0, -1, 0): '-y', (1, 0, 0): '+x', (-1, 0, 0): '-x'}
    r = tuple(int(round(c)) for c in d)
    if r in names and all(abs(c - r[i]) < 1e-6 for i, c in enumerate(d)):
        return names[r]
    return 'n%.4g/%.4g/%.4g' % d


def world(obj):
    """The vertices of a read_placed() object in the bed frame of its plate: the stored form turned by the build item
    (z up, the bed at the lowest vertex). The translation on the plate is left out."""
    lin = obj['linear']
    return obj['vertices'] if lin == _IDENTITY else [mat_apply(lin, v) for v in obj['vertices']]


# ---- the bed face of one STL in the registered projects --------------------------------------------------------------

def _memory(root):
    data = gate_common.read_json(gate_common.gate_path(root, *MEMORY), {})
    return data if isinstance(data, dict) else {}


def _remember(root, stl_rel, rows):
    """Keep the measured rows of this STL (by project file, object and pose) for the time its geometry changes before the
    project is written again. Never raises."""
    try:
        data = _memory(root)
        slot = data.get(stl_rel) if isinstance(data.get(stl_rel), list) else []
        same = lambda r, n: r.get('file') == n['table'] and r.get('object') == n['object']
        R = lambda n: [round(c, 9) for c in n['measured']['R']]
        if all(any(same(r, n) and r.get('pose') == n['measured']['pose'] and r.get('down') == [round(c, 9) for c in n['down']] and r.get('R') == R(n)
                   for r in slot) for n in rows):
            return                                       # on record already: the file is not written at every reading
        keep = [r for r in slot if isinstance(r, dict) and not any(same(r, n) for n in rows)]
        for n in rows:
            keep.append({'file': n['table'], 'project': n['project'], 'object': n['object'], 'plate': n['plate'], 'pose': n['measured']['pose'],
                         'model': n['measured'].get('model'), 'down': [round(c, 9) for c in n['down']], 'R': R(n),
                         'linear': [round(c, 9) for c in n['measured']['linear']], 'time': gate_common.now()})
        if keep != slot:
            data[stl_rel] = keep
            gate_common.write_json(gate_common.gate_path(root, *MEMORY), data)
    except Exception:
        pass


CACHE = ('run', 'placed_cache.json')


def _cache_key(root, layout, stl_rel):
    """What placed() of one STL depends on: the states of the files it reads (the STL, every registered .3mf, the memory
    of earlier measurements) and the code that measures (_code: this file, project_consistency, stl_mesh, meshid).
    None when the STL is not there."""
    def state(p):
        try:
            st = os.stat(p)
            return [st.st_size, st.st_mtime_ns]
        except OSError:
            return None
    stl = os.path.join(root, *str(stl_rel).replace(os.sep, '/').split('/'))
    if state(stl) is None:
        return None
    files = []
    for e in (layout or {}).get('projects') or []:
        if isinstance(e, dict) and e.get('file'):
            path = e['file'] if os.path.isabs(e['file']) else os.path.join(root, e['file'])
            files.append([e.get('key'), gate_common.rel(root, path), state(path)])
    return [state(stl), files, state(gate_common.gate_path(root, *MEMORY)), _code()]


def placed(root, layout, stl_rel, geometry=None):
    """placed_now() kept on disk (gate/run/placed_cache.json) per STL, the states of the files it reads and the code
    that measured it: `gate.py status`, the digest and every verify of a stamp ask for the bed face of each part, and
    reading every registered .3mf again in each new process costs seconds in a project of several large ones.
    While a pending archive is read (pending()), the rows of its project are measured on that archive and the rows of
    every other project come from the cache as usual. A measurement against another geometry than the STL on disk
    (`geometry`, the STL an export is about to write) is neither kept nor read from the cache."""
    if geometry is not None:
        return placed_now(root, layout, stl_rel, geometry=geometry)
    rows, problems, projects = _placed_on_disk(root, layout, stl_rel)
    pend = [src for src in _sources(root, layout) if src[3] is not None]
    if not pend or not stl_rel:
        return rows, problems, projects
    rels, keys = {src[1] for src in pend}, {src[0] for src in pend}
    rows2, problems2, projects2 = placed_now(root, layout, stl_rel, sources=pend)
    return ([r for r in rows if r.get('table') not in rels] + rows2,
            [p for p in problems if not any(str(p).startswith(x) for x in rels)] + problems2, (set(projects) - keys) | projects2)


def _placed_on_disk(root, layout, stl_rel):
    """placed_now() of the files on disk, through the cache."""
    disk = lambda: _sources(root, layout, use_pending=False)
    key = _cache_key(root, layout, stl_rel) if stl_rel else None
    path = gate_common.gate_path(root, *CACHE)
    if key is not None:
        try:
            data = gate_common.read_json(path, {})
        except gate_common.GateError:
            data = {}
        got = data.get(str(stl_rel)) if isinstance(data, dict) else None
        if isinstance(got, dict) and got.get('key') == key:
            return got.get('rows') or [], got.get('problems') or [], set(got.get('projects') or [])
    rows, problems, projects = placed_now(root, layout, stl_rel, sources=disk())
    if key is not None:
        try:
            key = _cache_key(root, layout, stl_rel)          # placed_now may have written the memory
            data = gate_common.read_json(path, {})
            data = data if isinstance(data, dict) else {}
            data[str(stl_rel)] = {'key': key, 'rows': rows, 'problems': problems, 'projects': sorted(p for p in projects if p is not None)}
            gate_common.write_json(path, data)
        except Exception:
            pass
    return rows, problems, projects


def _sources(root, layout, use_pending=True):
    """[(project key, the .3mf relative to the root, what read_placed() reads, a key for the motion cache)] of the
    registered projects, with a pending archive (pending()) in place of its file on disk, and a pending archive of a
    file that is no registered project as one more. use_pending False: the files on disk only."""
    out, seen = [], set()
    for entry in (layout or {}).get('projects') or []:
        if not isinstance(entry, dict) or not entry.get('file'):
            continue
        path = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, entry['file'])
        real = os.path.realpath(path)
        rel_file = gate_common.rel(root, path)
        if rel_file in seen:
            continue
        seen.add(rel_file)
        p = _PENDING.get(real) if use_pending else None
        if p is not None:
            seen.add(real)
            out.append((entry.get('key'), rel_file, p['files'], ('pending', real, id(p['token']))))
        elif os.path.isfile(path):
            out.append((entry.get('key'), rel_file, path, None))
    for real, p in sorted(_PENDING.items()) if use_pending else ():
        if real not in seen:
            rel_file = gate_common.rel(root, real)
            out.append((p.get('key') or rel_file, rel_file, p['files'], ('pending', real, id(p['token']))))
    return out


def placed_now(root, layout, stl_rel, geometry=None, sources=None):
    """How the registered .3mf projects lay the part whose STL is `stl_rel` (relative to the root), measured on the saved
    files (a pending archive in place of its file while pending() is active): (rows, problems, projects). A row is
    {"project": key, "table": the .3mf (relative), "plate", "object", "orient": text that says how it was measured,
    "down": outward normal of the bed face in the STL frame, "source": "project" | "project, earlier geometry",
    "measured": {"method", "worst", "pose", "triangles", "model"}}.
    `projects` are the keys of the projects that place this STL's geometry or its last measured mesh (their build tables
    are not asked). `problems` say why a placed object could not be measured (a scaled object).

    geometry: the STL an export is about to write (absolute path), when it is not the file at stl_rel. An object that is
    that geometry moved as a rigid body is measured on it ("project"). An object that is the STL still at stl_rel (the
    earlier geometry: an older export, or an STL of an older exporter while the model changed since) is measured on that
    file and is "project, earlier geometry": the face on the bed is known from the placement (both STLs are in the frame
    of the document), but the new geometry was never placed, pre-checked or sliced. The memory of earlier measurements
    counts the same way.
    sources: the projects to read (_sources), default all of them."""
    rows, problems, projects = [], [], set()
    if not stl_rel:
        return rows, problems, projects
    stl_path = os.path.join(root, *str(stl_rel).replace(os.sep, '/').split('/'))
    sV = sF = stl_key = None
    if os.path.isfile(stl_path):
        try:
            sV, sF = _stl(stl_path)
            stl_key = _file_key(stl_path)
        except Exception as e:
            problems.append('%s cannot be read as an STL (%s)' % (stl_rel, type(e).__name__))
            if geometry is None:
                return rows, problems, projects
    gV = gF = g_key = None
    if geometry is not None:
        try:
            gV, gF = _stl(geometry)
            g_key = _file_key(geometry)
        except Exception as e:
            problems.append('the STL about to be exported cannot be read (%s)' % type(e).__name__)
            return rows, problems, projects
        if sF is not None and gate_common.sha256_file(geometry) == gate_common.sha256_file(stl_path):
            sV, sF, stl_key = gV, gF, g_key              # the same file: nothing earlier about it
            gV = gF = g_key = None
    if sF is None and gF is None:
        return rows, problems, projects
    meshid = gate_common.sibling('meshid')
    models = {}

    def model_of(which):
        if which not in models:
            V, F = (gV, gF) if which == 'new' else (sV, sF)
            models[which] = meshid.geom_hash(V, len(F))
        return models[which]
    remembered = _memory(root).get(str(stl_rel)) or []
    fresh_disk = []                                  # rows measured on the STL on disk, in a file on disk: the memory keeps them
    for key_, rel_file, source, pkey_ in (sources if sources is not None else _sources(root, layout)):
        try:
            if pkey_ is not None:                        # a pending archive: read once per save, not once per stamp it holds
                if pkey_ not in _PENDING_READ:
                    _PENDING_READ[pkey_] = read_placed(source)
                data = _PENDING_READ[pkey_]
            else:
                data = read_placed(source)
            pkey = pkey_ or _file_key(source)
        except Exception as e:
            problems.append('%s cannot be read (%s)' % (rel_file, type(e).__name__))
            continue
        matched = set()
        for o in data['objects']:
            got, which = None, None
            for which_, V, F, fk in (('new', gV, gF, g_key), ('disk', sV, sF, stl_key)):
                if F is None or len(o['triangles']) != len(F):
                    continue
                mk = (pkey, o['id'], fk)
                if mk not in _MOTIONS:
                    if len(_MOTIONS) > 4096:
                        _MOTIONS.clear()
                    _MOTIONS[mk] = solve(V, F, o['vertices'], o['triangles'])
                if _MOTIONS[mk] is not None:
                    got, which = _MOTIONS[mk], which_
                    break
            if got is None:
                continue
            matched.add(o['name'])
            projects.add(key_)
            if not is_rotation(o['linear']):
                problems.append('%s: %s is scaled or sheared by its build item: its bed face is not measured' % (rel_file, o['name']))
                continue
            down = down_in_stl(got['R'], o['linear'])
            earlier = which == 'disk' and gF is not None
            row = {'project': key_, 'table': rel_file, 'plate': o['plate'], 'object': o['name'],
                   'orient': ('measured: face %s of the STL on the bed (%s, within %.3g mm)' % (_face_name(down), got['method'], got['worst'])
                              if not earlier else
                              'measured on the STL in the folder before this export (%s, within %.3g mm): face %s on the bed; the new geometry is not placed yet'
                              % (got['method'], got['worst'], _face_name(down))),
                   'down': down, 'source': 'project, earlier geometry' if earlier else 'project',
                   'measured': {'method': got['method'], 'worst': got['worst'], 'pose': o['pose'], 'triangles': len(o['triangles']), 'model': model_of(which),
                                'R': list(got['R']), 'linear': list(o['linear'])}}
            rows.append(row)
            if which == 'disk' and pkey_ is None:
                fresh_disk.append(row)
        for r in remembered:
            if not isinstance(r, dict) or r.get('file') != rel_file or r.get('object') in matched:
                continue
            o = next((o for o in data['objects'] if o['name'] == r.get('object') and o['pose'] == r.get('pose')), None)
            Rm = r.get('R')
            if o is not None and not is_rotation(o['linear']):
                problems.append('%s: %s is scaled or sheared by its build item: its bed face is not measured' % (rel_file, o['name']))
                continue
            if o is None or not (isinstance(Rm, list) and len(Rm) == 9 and all(isinstance(c, (int, float)) for c in Rm)):
                continue                                 # a memory without the motion of its mesh (an older gate) cannot follow a turn: not used
            # the stored vertices are the same (same pose), so R still sends the STL frame onto them; the build item may have
            # been turned since (a hand turn in the slicer lives there): the face on the bed follows the turn of today
            d = list(down_in_stl(tuple(float(c) for c in Rm), o['linear']))
            projects.add(key_)
            rows.append({'project': key_, 'table': rel_file, 'plate': o['plate'], 'object': o['name'],
                         'orient': 'measured %s on the mesh still placed (model %s), before this STL changed: face %s on the bed'
                                   % (str(r.get('time') or '')[:10], str(r.get('model') or '?')[:12], _face_name(tuple(d))),
                         'down': tuple(float(c) for c in d), 'source': 'project, earlier geometry',
                         'measured': {'method': 'remembered', 'worst': None, 'pose': o['pose'], 'triangles': len(o['triangles']), 'model': r.get('model'),
                                      'turned_since': [round(c, 9) for c in o['linear']] != r.get('linear')}})
    if fresh_disk and not _PENDING:
        _remember(root, str(stl_rel), fresh_disk)
    return rows, problems, projects
