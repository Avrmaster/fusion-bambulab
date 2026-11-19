"""Identity of a mesh: one definition for the gate (stamps, the guard) and for the print ledger. Standard library only.

geom_hash   sha1 over the sorted SET of vertices quantized to 0.001 mm, plus the triangle count, in the frame the mesh is
            given in. Exact: a hole that moves changes it. Two forms are stored for a mesh: "model" (the STL in the model
            frame) and "pose" (the mesh as it sits in a .3mf).
vset_hash   the same without the triangle count: survives a weld that only merges triangles.
shape_id    a coarse hint, "the same shape in another pose": triangle count, volume, area, sorted bounding-box sizes.
            Never enough to call a mesh unchanged: a hole moved inside the part keeps all four, and so does a mirror.

Two things to know about the hashes:
  - Compare a hash only with a hash made from the same source. The model form is always read from the STL file
    (read_stl), the pose form always from the text of the .3mf (project_meshes): a coordinate that sits exactly on a
    half quantum (12.3455) may round the other way after a float32 or a nine-digit round trip.
  - A mesh turned or moved has another geom_hash. turned_hashes() gives the hashes of the 24 quarter-turn poses, which
    is how an STL that was turned onto its bed face is found again; a free angle is not found.

Reading a .3mf (project_meshes, read_project) follows tools/bambu/bambu3mf.py: object names, parts and plates from
Metadata/model_settings.config, components and build items from 3D/3dmodel.model, meshes from 3D/Objects/*.model.

This file is vendored to bambulab/tools/ beside bambu3mf.py and imports nothing from the plugin."""
import hashlib, itertools, math, re, struct, zipfile

QUANTUM = 0.001          # mm


# ---- STL -------------------------------------------------------------------------------------------------------------

def _binary_corners(d):
    """Corner coordinates of a binary STL as a flat tuple of 9 floats per triangle, or None when the size does not fit."""
    if len(d) < 84:
        return None
    n = struct.unpack_from('<I', d, 80)[0]
    if len(d) < 84 + 50 * n:
        return None
    out = []
    for i in range(n):
        out.append(struct.unpack_from('<9f', d, 84 + 50 * i + 12))
    return out


def _ascii_corners(d):
    text = d.decode('latin-1')
    if not re.match(r'\s*solid\b', text) or 'facet' not in text:
        return None
    nums = re.findall(r'vertex\s+(\S+)\s+(\S+)\s+(\S+)', text)
    if not nums or len(nums) % 3:
        return None
    try:
        pts = [(float(a), float(b), float(c)) for a, b, c in nums]
    except ValueError:
        return None
    return [pts[i] + pts[i + 1] + pts[i + 2] for i in range(0, len(pts), 3)]


def read_stl(path):
    """Read a binary or ASCII STL. Returns (vertices, triangles): vertices a list of (x, y, z) floats, each distinct
    coordinate triple once (exact equality, coordinates under 1e-9 count as 0.0); triangles a list of (a, b, c) indices
    with degenerate ones dropped. ValueError when the file is not an STL."""
    with open(path, 'rb') as f:
        d = f.read()
    exact = len(d) >= 84 and len(d) == 84 + 50 * struct.unpack_from('<I', d, 80)[0]
    corners = _binary_corners(d) if exact else None
    if corners is None:
        corners = _ascii_corners(d)
    if corners is None and not exact and not d[:5].lower() == b'solid':
        corners = _binary_corners(d)                    # a binary file with bytes after the last triangle
    if corners is None:
        raise ValueError('%s is not an STL file' % path)
    vid, V, F = {}, [], []
    for c in corners:
        t = []
        for k in (0, 3, 6):
            q = tuple(0.0 if abs(x) < 1e-9 else x for x in c[k:k + 3])
            if q[0] != q[0] or q[1] != q[1] or q[2] != q[2]:        # NaN
                raise ValueError('%s holds a coordinate that is not a number' % path)
            i = vid.get(q)
            if i is None:
                i = vid[q] = len(V); V.append(q)
            t.append(i)
        if len(set(t)) == 3:
            F.append(tuple(t))
    return V, F


# ---- hashes ----------------------------------------------------------------------------------------------------------

def quantize(vertices, quantum=QUANTUM):
    """The sorted set of vertices as integer triples (round(x / quantum), ...)."""
    return sorted({(int(round(x / quantum)), int(round(y / quantum)), int(round(z / quantum))) for x, y, z in vertices})


def _sha1(q, quantum, tail):
    h = hashlib.sha1(('meshid 1 quantum %r\n' % (quantum,)).encode())
    for i in range(0, len(q), 20000):
        h.update(''.join('%d %d %d\n' % t for t in q[i:i + 20000]).encode())
    h.update(tail.encode())
    return h.hexdigest()


def geom_hash(vertices, n_triangles, quantum=QUANTUM):
    """40 hex characters: sha1 over quantize(vertices) and the triangle count. Does not depend on the order of the
    vertices or of the triangles."""
    return _sha1(quantize(vertices, quantum), quantum, 'triangles %d\n' % int(n_triangles))


def vset_hash(vertices, quantum=QUANTUM):
    """40 hex characters: sha1 over quantize(vertices) only."""
    return _sha1(quantize(vertices, quantum), quantum, 'vertices only\n')


def _quarter_turns():
    """The 24 rotations that map the axes onto the axes: (permutation, signs) with determinant +1, identity first."""
    out = []
    for p in itertools.permutations((0, 1, 2)):
        even = p in ((0, 1, 2), (1, 2, 0), (2, 0, 1))
        for s in itertools.product((1, -1), repeat=3):
            if (s[0] * s[1] * s[2] > 0) == even:
                out.append((p, s))
    out.sort(key=lambda r: r != ((0, 1, 2), (1, 1, 1)))
    return out


QUARTER_TURNS = _quarter_turns()


def turned_hashes(vertices, n_triangles, quantum=QUANTUM):
    """geom_hash of the mesh in each of its 24 quarter-turn poses about the origin, the pose as given first. Exact:
    a quarter turn only swaps and negates coordinates. Use it to find the model-frame STL of an STL that was turned onto
    a bed face. It does not cover a move, a free angle or a mirror."""
    q = quantize(vertices, quantum); tail = 'triangles %d\n' % int(n_triangles); out = []
    for p, s in QUARTER_TURNS:
        out.append(_sha1(sorted((s[0] * t[p[0]], s[1] * t[p[1]], s[2] * t[p[2]]) for t in q), quantum, tail))
    return out


def shape_id(vertices, triangles):
    """{"tris": n, "vol": mm3 rounded to 0.01, "area": mm2 rounded to 0.01, "box": [a, b, c] the bounding-box sizes sorted
    and rounded to 0.01}: equal for the same shape turned by quarter turns or moved."""
    if not vertices:
        return {'tris': len(triangles), 'vol': 0.0, 'area': 0.0, 'box': [0.0, 0.0, 0.0]}
    lo = [min(v[i] for v in vertices) for i in range(3)]; hi = [max(v[i] for v in vertices) for i in range(3)]
    P = [(v[0] - lo[0], v[1] - lo[1], v[2] - lo[2]) for v in vertices]      # from the box corner: a move changes nothing
    vol, area = [], []
    for a, b, c in triangles:
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = P[a], P[b], P[c]
        vol.append(x0 * (y1 * z2 - z1 * y2) - y0 * (x1 * z2 - z1 * x2) + z0 * (x1 * y2 - y1 * x2))
        ux, uy, uz, wx, wy, wz = x1 - x0, y1 - y0, z1 - z0, x2 - x0, y2 - y0, z2 - z0
        area.append(math.sqrt((uy * wz - uz * wy) ** 2 + (uz * wx - ux * wz) ** 2 + (ux * wy - uy * wx) ** 2))
    return {'tris': len(triangles), 'vol': round(abs(math.fsum(vol)) / 6, 2) + 0.0, 'area': round(math.fsum(area) / 2, 2) + 0.0,
            'box': sorted(round(hi[i] - lo[i], 2) + 0.0 for i in range(3))}


def same_shape(a, b, tol=0.001, box=True):
    """Do two shape_id() values agree: equal triangle count, and volume, area and box sizes within the relative
    tolerance `tol`. A hint only. box=False leaves the box sizes out: a part turned by a free angle on the bed keeps its
    volume and area, not its bounding box."""
    if not a or not b or a.get('tris') != b.get('tris'):
        return False
    near = lambda x, y: abs(x - y) <= tol * max(abs(x), abs(y)) + 0.011        # 0.011: both numbers are rounded to 0.01
    if not near(a['vol'], b['vol']) or not near(a['area'], b['area']):
        return False
    return not box or all(near(x, y) for x, y in zip(a['box'], b['box']))


def _unique_points(vertices, q=1e-4):
    seen = {}
    for v in vertices:
        k = (int(round(v[0] / q)), int(round(v[1] / q)), int(round(v[2] / q)))
        if k not in seen:
            seen[k] = (float(v[0]), float(v[1]), float(v[2]))
    return list(seen.values())


def _frame(c, p1, p2):
    """A right-handed frame (three unit rows) from the centre c and two points; None when they lie on one line."""
    a = (p1[0] - c[0], p1[1] - c[1], p1[2] - c[2]); b = (p2[0] - c[0], p2[1] - c[1], p2[2] - c[2])
    la = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    if la < 1e-9:
        return None
    e1 = (a[0] / la, a[1] / la, a[2] / la)
    d = b[0] * e1[0] + b[1] * e1[1] + b[2] * e1[2]
    w = (b[0] - d * e1[0], b[1] - d * e1[1], b[2] - d * e1[2])
    lw = math.sqrt(w[0] * w[0] + w[1] * w[1] + w[2] * w[2])
    if lw < 1e-6:
        return None
    e2 = (w[0] / lw, w[1] / lw, w[2] / lw)
    return e1, e2, (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])


def rigid_copy(a, b, tol=0.002, tries=4000):
    """Is the vertex cloud `b` the cloud `a` moved as a rigid body: turned by any angle and shifted, not mirrored, not
    scaled, and with every vertex at its place? A PROOF, not a hint: two parts with the same triangle count, volume and
    area (a hole that moved by 2 mm keeps all three) are told apart, because every vertex of `a` must land on a vertex
    of `b` within `tol` mm.

    How: both clouds are reduced to their distinct vertices; the distances to the centroid must agree as sorted lists;
    then the motion is solved from the centroid and two vertices of `a` (the farthest one, and the one that spans the
    largest triangle with it) against every pair of `b` with the same distances, and the first motion that carries a
    sample of `a` onto `b` is checked on all of `a`. `tries` bounds the candidate pairs: a cloud too regular to settle
    in that many tries gives False (not proven is not a copy)."""
    A, B = _unique_points(a), _unique_points(b)
    if len(A) != len(B) or len(A) < 4:
        return False
    n = float(len(A))
    ca = tuple(math.fsum(v[i] for v in A) / n for i in range(3)); cb = tuple(math.fsum(v[i] for v in B) / n for i in range(3))
    dist = lambda p, q: math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)
    da = [dist(v, ca) for v in A]; db = [dist(v, cb) for v in B]
    if max(abs(x - y) for x, y in zip(sorted(da), sorted(db))) > tol:
        return False
    i1 = max(range(len(A)), key=lambda i: da[i])

    def spread(i):                                      # twice the area of the triangle centre, A[i1], A[i]
        u = (A[i1][0] - ca[0], A[i1][1] - ca[1], A[i1][2] - ca[2]); w = (A[i][0] - ca[0], A[i][1] - ca[1], A[i][2] - ca[2])
        return (u[1] * w[2] - u[2] * w[1]) ** 2 + (u[2] * w[0] - u[0] * w[2]) ** 2 + (u[0] * w[1] - u[1] * w[0]) ** 2
    i2 = max(range(len(A)), key=spread)
    fa = _frame(ca, A[i1], A[i2])
    if fa is None:
        return False
    cell = max(4 * tol, 0.01)
    grid = {}
    for v in B:
        grid.setdefault((int(math.floor(v[0] / cell)), int(math.floor(v[1] / cell)), int(math.floor(v[2] / cell))), []).append(v)

    def lands(p):
        ks = [sorted({int(math.floor((p[i] - tol) / cell)), int(math.floor((p[i] + tol) / cell))}) for i in range(3)]
        for kx in ks[0]:
            for ky in ks[1]:
                for kz in ks[2]:
                    for v in grid.get((kx, ky, kz), ()):
                        if abs(v[0] - p[0]) <= tol and abs(v[1] - p[1]) <= tol and abs(v[2] - p[2]) <= tol:
                            return True
        return False
    d12 = dist(A[i1], A[i2])
    step = max(1, len(A) // 24)
    sample = A[::step][:32]
    first = [j for j in range(len(B)) if abs(db[j] - da[i1]) <= tol]
    second = [j for j in range(len(B)) if abs(db[j] - da[i2]) <= tol]
    done = 0
    for j1 in first:
        for j2 in second:
            if abs(dist(B[j1], B[j2]) - d12) > tol:
                continue
            done += 1
            if done > tries:
                return False
            fb = _frame(cb, B[j1], B[j2])
            if fb is None:
                continue

            def move(p):                                # into the frame of `a`, out of the frame of `b`
                x = (p[0] - ca[0], p[1] - ca[1], p[2] - ca[2])
                k = [x[0] * e[0] + x[1] * e[1] + x[2] * e[2] for e in fa]
                return tuple(cb[i] + k[0] * fb[0][i] + k[1] * fb[1][i] + k[2] * fb[2][i] for i in range(3))
            if all(lands(move(p)) for p in sample) and all(lands(move(p)) for p in A):
                return True
    return False


def both_hashes(vertices, n_triangles, quantum=QUANTUM):
    """(geom_hash, vset_hash) of one mesh with one pass over its vertices."""
    q = quantize(vertices, quantum)
    return _sha1(q, quantum, 'triangles %d\n' % int(n_triangles)), _sha1(q, quantum, 'vertices only\n')


def mesh_ids(vertices, triangles):
    """{"geom_hash", "vset_hash", "shape_id", "triangles": n} of a mesh in memory."""
    g, v = both_hashes(vertices, len(triangles))
    return {'geom_hash': g, 'vset_hash': v, 'shape_id': shape_id(vertices, triangles), 'triangles': len(triangles)}


def stl_ids(path):
    """{"sha256": of the file bytes, "geom_hash", "vset_hash", "shape_id", "triangles": n} of an STL file
    (the "model" form when the STL is in the model frame)."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    V, F = read_stl(path)
    return dict(mesh_ids(V, F), sha256=h.hexdigest())


# ---- meshes of a .3mf ------------------------------------------------------------------------------------------------

_NUM = r'([-+\d.eE]+)'
_VERTEX = re.compile(r'<vertex\s+x="%s"\s+y="%s"\s+z="%s"' % (_NUM, _NUM, _NUM))
_TRIANGLE = re.compile(r'<triangle\s+v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"')
_OBJECT = re.compile(r'<object\s+id="(\d+)"[^>]*>(.*?)</object>', re.S)
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


def xml_mesh(text):
    """(vertices, triangles) of the first <mesh> in a piece of 3MF model text: every <vertex> in file order as (x, y, z)
    floats and every <triangle> as (v1, v2, v3). Nothing is welded and nothing is dropped."""
    m = re.search(r'<mesh>(.*?)</mesh>', text, re.S)
    body = m.group(1) if m else text
    return ([(float(x), float(y), float(z)) for x, y, z in _VERTEX.findall(body)],
            [(int(a), int(b), int(c)) for a, b, c in _TRIANGLE.findall(body)])


def _text(files, name):
    data = files.get(name)
    if data is None:
        return None
    return data.decode('utf-8', 'replace') if isinstance(data, (bytes, bytearray)) else data


def _transform(text):
    try:
        t = tuple(float(x) for x in (text or '').split())
    except ValueError:
        return _IDENTITY
    return t if len(t) == 12 else _IDENTITY


def _apply(t, V):
    if t == _IDENTITY:
        return V
    return [(x * t[0] + y * t[3] + z * t[6] + t[9], x * t[1] + y * t[4] + z * t[7] + t[10], x * t[2] + y * t[5] + z * t[8] + t[11]) for x, y, z in V]


def project_meshes(files):
    """The printable-part meshes of a Bambu Studio project, one entry per object, read the way bambu3mf.py reads them.

    files  {name in the archive: bytes}, as bambu3mf.load() returns and bambu3mf.save() is about to write

    Returns [{"object": name (Metadata/model_settings.config), "id": object id, "plate": plate name or None (the object
    is on no plate), "plate_index": 1-based or None, "printable": bool (the build item's flag),
    "vertices": [(x, y, z)], "triangles": [(a, b, c)], "parts": n}] in the order of the model file.
    Vertices are the stored ones (the "pose" form): the bed orientation is in them, the position on the plate is not
    (that is the transform of the build item). Only parts of subtype normal_part count: modifiers and support blockers are
    left out. An object of several normal parts is one entry, each part moved by its component transform.
    An object with no mesh is left out. A file that is not a project gives []."""
    model = _text(files, '3D/3dmodel.model')
    if model is None:
        return []
    cfg = _text(files, 'Metadata/model_settings.config') or ''
    names, normal = {}, {}
    for oid, body in re.findall(r'<object id="(\d+)">(.*?)</object>', cfg, re.S):
        m = re.search(r'key="name" value="([^"]*)"', body.split('<part')[0])
        names[oid] = m.group(1) if m else None
        normal[oid] = set(re.findall(r'<part id="(\d+)" subtype="normal_part"', body))
    plate_of = {}
    for i, p in enumerate(re.findall(r'<plate>(.*?)</plate>', cfg, re.S)):
        m = re.search(r'key="plater_id" value="(\d+)"', p); n = re.search(r'key="plater_name" value="([^"]*)"', p)
        for oid in re.findall(r'key="object_id" value="(\d+)"', p):
            plate_of.setdefault(oid, (n.group(1) if n else '', int(m.group(1)) if m else i + 1))
    printable = {}
    for tag in re.findall(r'<item\s[^>]*>', model):
        a = dict(_ATTR.findall(tag))
        if 'objectid' in a:
            printable[a['objectid']] = a.get('printable', '1') != '0'
    cache = {}

    def mesh_of(path, mid):
        if path not in cache:
            text = _text(files, path); cache[path] = {}
            for m in _OBJECT.finditer(text or ''):
                if '<mesh>' in m.group(2):
                    cache[path][m.group(1)] = m.group(2)
        body = cache[path].get(mid)
        if isinstance(body, str):
            body = cache[path][mid] = xml_mesh(body)
        return body

    out = []
    for m in _OBJECT.finditer(model):
        oid, body = m.group(1), m.group(2)
        V, F, parts = [], [], 0
        if '<mesh>' in body:                            # a plain 3MF: the mesh lies in the object itself
            V, F = xml_mesh(body); parts = 1
        for tag in re.findall(r'<component\s[^>]*>', body):
            a = dict(_ATTR.findall(tag)); mid = a.get('objectid')
            if oid in normal and mid not in normal[oid]:
                continue                                # a modifier or a support blocker
            got = mesh_of((a.get('p:path') or '3D/3dmodel.model').lstrip('/'), mid)
            if not got or not got[0]:
                continue
            PV = _apply(_transform(a.get('transform')), got[0]); base = len(V)
            V = V + PV if V else PV
            F = F + [(i + base, j + base, k + base) for i, j, k in got[1]] if base else got[1]
            parts += 1
        if not V or oid not in printable and oid not in names:
            continue                                    # a mesh resource that is only a component of another object
        plate = plate_of.get(oid)
        out.append({'object': names.get(oid) or 'object %s' % oid, 'id': oid, 'plate': plate[0] if plate else None,
                    'plate_index': plate[1] if plate else None, 'printable': printable.get(oid, True),
                    'vertices': V, 'triangles': F, 'parts': parts})
    return out


def read_project(path):
    """project_meshes() of a .3mf file on disk. Raises OSError / zipfile.BadZipFile when it cannot be read."""
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()
                 if n in ('3D/3dmodel.model', 'Metadata/model_settings.config') or n.startswith('3D/Objects/')}
    return project_meshes(files)


def pose_hashes(meshes):
    """{object name: geom_hash of the mesh as placed} for a list of project_meshes() entries. Two objects of one name:
    the later one wins; use the list itself when that matters."""
    return {m['object']: geom_hash(m['vertices'], len(m['triangles'])) for m in meshes}
