"""Wall-thickness check on printed-part STLs: finds walls, straps and necks thinner than MIN mm.
    <python with numpy + scipy> bambulab/tools/wall_thickness.py [--min 1.5] [--voxel 0.12] <part.stl> ...
Exit 1 if any part has a hit. Coordinates are the STL's (Fusion world, mm).

The Fusion `thin()` check looks at the width of each face, not at the material between faces, so it can pass a 0.86 mm neck
between a bore corner and a notch. Slicing in 2D does not work either: a corner-to-corner neck survives a 2D opening, and a
2 mm step that is thick in 3D looks thin in a slice. So this works on voxels:
  - the part is voxelized (ray parity per column) and eroded by r = MIN/2 with a Euclidean distance transform;
  - neck: a solid piece falls apart into several pieces of real size, or vanishes (all of it is thinner than MIN);
  - sheet: the opening (erode, then grow back by r) removes a wall or fin. Every sound convex edge also loses a thin
    rounding (cross-section ~0.2 r² at 90°), so a removed voxel counts only where the removed material fills more
    than SHEET_FRAC of a box of side 4r around it: sheets do, edges do not (edges sharper than ~50° do: knife edges).
Reading the result: a neck is always real. Sheets need a look: walls designed at exactly 1.4-1.5 read ~1.3-1.46 (voxel
rounding), shims and washers are thin on purpose, and big parts get coarse voxels. Sheets under 2 mm³ (rims around pockets)
are listed only with --all."""
import sys, struct, argparse
import numpy as np
from scipy import ndimage

SHEET_FRAC = 0.06
MAX_VOX = 40e6
EPS = 1e-4   # grid offset, so no column runs exactly through a mesh edge


def load(path):
    b = open(path, 'rb').read()
    n = struct.unpack('<I', b[80:84])[0]
    a = np.frombuffer(b[84:84 + 50 * n], dtype=np.dtype([('n', '<f4', 3), ('v', '<f4', (3, 3)), ('a', '<u2')]))
    return a['v'].astype(np.float64)


def voxelize(T, h):
    lo = T.reshape(-1, 3).min(0) - 2 * h
    hi = T.reshape(-1, 3).max(0) + 2 * h
    n = np.ceil((hi - lo) / h).astype(int)
    cx = lo[0] + (np.arange(n[0]) + 0.5) * h + EPS
    cy = lo[1] + (np.arange(n[1]) + 0.5) * h + EPS * 1.618
    cols, zs = [], []
    for a, b, c in T:
        x0, x1 = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
        y0, y1 = min(a[1], b[1], c[1]), max(a[1], b[1], c[1])
        i0, i1 = np.searchsorted(cx, x0), np.searchsorted(cx, x1)
        j0, j1 = np.searchsorted(cy, y0), np.searchsorted(cy, y1)
        if i0 >= i1 or j0 >= j1:
            continue
        X, Y = np.meshgrid(cx[i0:i1], cy[j0:j1], indexing='ij')
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-12:
            continue
        l1 = ((b[1] - c[1]) * (X - c[0]) + (c[0] - b[0]) * (Y - c[1])) / den
        l2 = ((c[1] - a[1]) * (X - c[0]) + (a[0] - c[0]) * (Y - c[1])) / den
        l3 = 1 - l1 - l2
        m = (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        if not m.any():
            continue
        I, J = np.nonzero(m)
        cols.append((I + i0) * n[1] + (J + j0))
        zs.append(l1[m] * a[2] + l2[m] * b[2] + l3[m] * c[2])
    col = np.concatenate(cols)
    z = np.concatenate(zs)
    o = np.lexsort((z, col))
    col, z = col[o], z[o]
    S = np.zeros(n, dtype=bool)
    k = ((z - lo[2]) / h - 0.5)
    # pairs of hits per column: inside between hit 2m and 2m+1
    starts = np.r_[0, np.nonzero(np.diff(col))[0] + 1]
    ends = np.r_[starts[1:], len(col)]
    odd = (ends - starts) % 2 == 1
    delta = np.zeros((n[0] * n[1], n[2] + 1), dtype=np.int16)
    for s, e, bad in zip(starts, ends, odd):
        if bad:
            continue   # an odd hit count (a seam): leave this column empty rather than fill it wrong
        for p in range(s, e, 2):
            k0 = int(np.ceil(k[p]))
            k1 = int(np.floor(k[p + 1])) + 1
            if k1 > k0:
                delta[col[p], max(k0, 0)] += 1
                delta[col[p], min(k1, n[2])] -= 1
    S = (np.cumsum(delta, axis=1)[:, :n[2]] > 0).reshape(n)
    return S, lo, int(odd.sum())


def check(path, wmin, h, min_sheet=2.0):
    T = load(path)
    ext = T.reshape(-1, 3).max(0) - T.reshape(-1, 3).min(0)
    while np.prod(ext / h + 4) > MAX_VOX:
        h *= 1.25
    r = wmin / 2
    S, lo, odd = voxelize(T, h)
    at = lambda idx: '(%.1f, %.1f, %.1f)' % tuple(lo + (np.asarray(idx) + 0.5) * h)
    hits = []
    D = ndimage.distance_transform_edt(S, sampling=h)
    E = D > r + h / 2                      # voxel centres sit h/2 inside the surface on average
    min_vox = max(8, int((r / h) ** 3))    # pieces smaller than an r-cube are crumbs, not parts
    ls, ns = ndimage.label(S, structure=np.ones((3, 3, 3)))
    le, ne = ndimage.label(E, structure=np.ones((3, 3, 3)))
    size_e = ndimage.sum(np.ones_like(le), le, index=np.arange(1, ne + 1))
    real = np.zeros(ne + 1, dtype=bool)
    real[1:] = size_e >= min_vox
    for s in range(1, ns + 1):
        inside = np.unique(le[(ls == s) & (le > 0)])
        inside = [e for e in inside if real[e]]
        if not inside:
            idx = np.argwhere(ls == s)
            hits.append(('thin piece', 'all of it < %.2f' % wmin, at(idx.min(0)), at(idx.max(0))))
        elif len(inside) > 1:
            # the neck: removed voxels within r + 2h of two different eroded pieces
            near = []
            for e in inside[:12]:
                near.append(ndimage.distance_transform_edt(le != e, sampling=h) <= r + 2 * h)
            cnt = np.sum(near, axis=0)
            idx = np.argwhere((cnt >= 2) & S & ~E)
            lab, nl = ndimage.label((cnt >= 2) & S & ~E, structure=np.ones((3, 3, 3)))
            for q in ndimage.find_objects(lab):
                a = [sl.start for sl in q]; b = [sl.stop - 1 for sl in q]
                hits.append(('neck', 'part splits into %d' % len(inside), at(a), at(b)))
    O = ndimage.distance_transform_edt(~E, sampling=h) <= r + h / 2
    R = S & ~O
    w = max(3, int(round(4 * r / h)) | 1)
    frac = ndimage.uniform_filter(R.astype(np.float32), size=w, mode='constant')
    F = R & (frac > SHEET_FRAC)
    lf, nf = ndimage.label(F, structure=np.ones((3, 3, 3)))
    for i, q in enumerate(ndimage.find_objects(lf), 1):
        vol = np.count_nonzero(lf[q] == i) * h ** 3
        if vol < min_sheet:
            continue
        a = [sl.start for sl in q]; b = [sl.stop - 1 for sl in q]
        dmax = 2 * (D[q][lf[q] == i].max() - h / 2)
        hits.append(('sheet', '%.1f mm3, thinnest ~%.2f' % (vol, max(dmax, h)), at(a), at(b)))
    return hits, h, odd


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--min', type=float, default=1.5, help='minimum wall, mm')
    ap.add_argument('--voxel', type=float, default=0.12, help='voxel size, mm (grows for big parts, see MAX_VOX)')
    ap.add_argument('--all', action='store_true', help='also list sheets under 2 mm³ (mostly rims around holes and pockets)')
    ap.add_argument('stl', nargs='+')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')   # part folders have Cyrillic names
    bad = 0
    for p in a.stl:
        hits, h, odd = check(p, a.min, a.voxel, 0.5 if a.all else 2.0)
        hits.sort(key=lambda t: t[0] != 'neck')
        note = ' (voxel %.3f%s)' % (h, ', %d open columns skipped' % odd if odd else '')
        print('%s: %s%s' % (p, 'OK' if not hits else '%d thin places' % len(hits), note))
        for kind, what, a0, a1 in hits:
            print('   %-10s %-28s %s .. %s' % (kind, what, a0, a1))
        bad += bool(hits)
    sys.exit(1 if bad else 0)
