"""Host-side probe: is an STL closed and one shell, and what is it (volume, bounding box, geometry hash).
Standard library only. No Fusion, no slicer.

    python3 gate/tools/probes/stl_mesh.py <file.stl>

The reader here (_read_stl) is also what stl_walls.py measures on, so both probes see one mesh."""
import json, os, re, struct, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import gate_common
except ImportError:                                  # run as a script: tools/gate (or gate/tools) is one folder up
    sys.path.insert(0, os.path.dirname(_HERE))
    import gate_common
from gate_common import GateError

_WELD = 1e-7             # mm: vertices closer than this are one vertex
_ZERO = 1e-9             # mm: a coordinate smaller than this is 0
_ASCII_VERTEX = re.compile(rb'vertex\s+(\S+)\s+(\S+)\s+(\S+)')


def _read_bytes(stl_path):
    if not os.path.isfile(str(stl_path)):
        raise GateError('missing STL: %s' % (stl_path,))
    with open(stl_path, 'rb') as f:
        return f.read()


def _corners(data, stl_path):
    """Triangles of an STL as an iterable of 9 floats (three corners). Binary when the size fits its count, else ASCII."""
    n = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else -1
    looks_ascii = data[:5].lower() == b'solid' and b'facet' in data[:2000]
    if n >= 0 and (84 + 50 * n == len(data) or (84 + 50 * n < len(data) and not looks_ascii)):
        return (t[3:12] for t in struct.iter_unpack('<12fH', data[84:84 + 50 * n]))
    if b'vertex' in data:
        try:
            nums = [float(x) for tri in _ASCII_VERTEX.findall(data) for x in tri]
        except ValueError:
            raise GateError('%s is not an STL: a vertex line holds something that is not a number' % (stl_path,))
        if nums and len(nums) % 9 == 0:
            return (tuple(nums[i:i + 9]) for i in range(0, len(nums), 9))
    raise GateError('%s is not an STL (neither a binary file of the size its header says nor ASCII facets)' % (stl_path,))


def _read_stl(stl_path, data=None):
    """(vertices, triangles, degenerate): vertices a list of (x, y, z), each spot once (the first coordinates seen win,
    coordinates under 1e-9 are 0.0, spots closer than 1e-7 mm are one vertex); triangles a list of (a, b, c) indices;
    degenerate = how many triangles were dropped because two corners are one vertex. GateError when it is no STL."""
    if data is None:
        data = _read_bytes(stl_path)
    seen, welded, V, F, dropped = {}, {}, [], [], 0
    get = seen.get
    inv = 1.0 / _WELD
    for t in _corners(data, stl_path):
        tri = []
        for q in (t[0:3], t[3:6], t[6:9]):
            i = get(q)
            if i is None:                            # a new float triple: weld it onto the 1e-7 grid
                x, y, z = q
                if x != x or y != y or z != z or abs(x) == float('inf') or abs(y) == float('inf') or abs(z) == float('inf'):
                    raise GateError('%s holds a coordinate that is not a number' % (stl_path,))
                k = (int(round(x * inv)), int(round(y * inv)), int(round(z * inv)))
                i = welded.get(k)
                if i is None:
                    i = welded[k] = len(V)
                    V.append((0.0 if abs(x) < _ZERO else x, 0.0 if abs(y) < _ZERO else y, 0.0 if abs(z) < _ZERO else z))
                seen[q] = i
            tri.append(i)
        a, b, c = tri
        if a == b or b == c or a == c:
            dropped += 1
        else:
            F.append((a, b, c))
    return V, F, dropped


def _edge_counts(F, nv, shells=False):
    """(open, non_manifold, flipped, shells): edges used by one triangle, by more than two, and by exactly two that run the
    same way (the two faces disagree about which side is outside). shells = connected shells through shared edges, or None
    when not asked for."""
    uses = {}                                        # undirected edge -> forward uses + 65536 * backward uses
    first = {}                                       # undirected edge -> first triangle (for the shells)
    parent = list(range(len(F))) if shells else None
    uget = uses.get
    for i, (a, b, c) in enumerate(F):
        for u, v in ((a, b), (b, c), (c, a)):
            if u < v:
                k = u * nv + v; uses[k] = uget(k, 0) + 1
            else:
                k = v * nv + u; uses[k] = uget(k, 0) + 65536
            if parent is not None:
                j = first.get(k)
                if j is None:
                    first[k] = i
                else:                                # union the two triangles
                    ri = i
                    while parent[ri] != ri:
                        parent[ri] = parent[parent[ri]]; ri = parent[ri]
                    rj = j
                    while parent[rj] != rj:
                        parent[rj] = parent[parent[rj]]; rj = parent[rj]
                    if ri != rj:
                        parent[ri] = rj
    opn = nonm = flip = 0
    for n in uses.values():
        fwd, back = n & 65535, n >> 16
        if fwd + back == 1: opn += 1
        elif fwd + back > 2: nonm += 1
        elif fwd != 1: flip += 1
    count = None
    if parent is not None:
        count = 0
        for i in range(len(F)):
            if parent[i] == i: count += 1
    return opn, nonm, flip, count


def _volume6(V, F):
    s = 0.0
    for a, b, c in F:
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = V[a], V[b], V[c]
        s += x0 * (y1 * z2 - z1 * y2) - y0 * (x1 * z2 - z1 * x2) + z0 * (x1 * y2 - y1 * x2)
    return s


def _bbox(V):
    xs, ys, zs = zip(*V)
    return [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]]


def measure(stl_path):
    """Read one STL (binary or ASCII) and return its facts.

    stl_path  path of the STL

    Returns {"stl": the path as given, "sha256": of the file bytes, "triangles": n (degenerate ones dropped),
             "degenerate": n dropped, "open_edges": n (edges used by one triangle), "non_manifold_edges": n (edges used by
             more than two), "shells": n connected shells, "volume": mm3, "bbox": [[x, y, z], [x, y, z]],
             "geom_hash": meshid.geom_hash of the mesh in the frame of the file (the "model" form)}.
    Vertices closer than 1e-7 mm are one vertex (coordinates under 1e-9 count as 0), as bambu3mf.read_stl does.
    Raises gate_common.GateError when the file is missing or is not an STL.

    Two keys more than listed above: "flipped_edges" (edges whose two triangles run the same way round: the faces
    disagree about which side is outside; stl_walls refuses such a mesh) and "inverted" (True when the whole mesh is
    inside out; "volume" is then still given as a positive number).
    Shells are counted through shared edges: two bodies that touch in one point are two shells.
    "geom_hash" is meshid.stl_ids(stl_path)["geom_hash"]: the one definition stamps and the guard use."""
    import hashlib
    data = _read_bytes(stl_path)
    V, F, dropped = _read_stl(stl_path, data)
    if not F:
        raise GateError('%s holds no triangle' % (stl_path,))
    opn, nonm, flip, shells = _edge_counts(F, len(V), shells=True)
    vol = _volume6(V, F) / 6.0
    box = _bbox([V[i] for i in set(i for t in F for i in t)])
    geom = gate_common.sibling('meshid').stl_ids(stl_path)['geom_hash']
    return {'stl': stl_path, 'sha256': hashlib.sha256(data).hexdigest(), 'triangles': len(F), 'degenerate': dropped,
            'open_edges': opn, 'non_manifold_edges': nonm, 'flipped_edges': flip, 'shells': shells,
            'volume': round(abs(vol), 4), 'inverted': vol < 0, 'bbox': [[round(c, 6) for c in p] for p in box],
            'geom_hash': geom}


def main(argv=None):
    """python3 stl_mesh.py <file.stl>: print the facts as JSON. Returns 0, or 4 when the file cannot be read."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0].startswith('-'):
        sys.stderr.write('usage: stl_mesh.py <file.stl>\n')
        return gate_common.EXIT_INFRA
    try:
        facts = measure(args[0])
    except GateError as e:
        sys.stderr.write('GATE ERROR  %s\n' % e)
        return gate_common.EXIT_INFRA
    print(json.dumps(facts, indent=1, sort_keys=True))
    return gate_common.EXIT_PASS


if __name__ == '__main__':
    sys.exit(main())
