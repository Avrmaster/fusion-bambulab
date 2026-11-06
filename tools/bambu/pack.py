"""Pack parts onto a Bambu bed by their real footprints (shapely), not by bounding boxes: a small part may sit in the free
corner of a ring or next to a cheek. Greedy bottom-left fill in 15° turns, then more plates when full; several part orders are
tried (biggest first, then shuffled) and the layout with the fewest plates, then the emptiest last plate, wins.
Needs shapely."""
import math
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.affinity import rotate, translate
from shapely.prepared import prep

def footprint(V, F, gap):
    """XY outline of a mesh (union of its projected triangles), grown by gap / 2 on every side."""
    tris = []
    for a, b, c in F:
        p = [(V[a][0], V[a][1]), (V[b][0], V[b][1]), (V[c][0], V[c][1])]
        if abs((p[1][0] - p[0][0]) * (p[2][1] - p[0][1]) - (p[2][0] - p[0][0]) * (p[1][1] - p[0][1])) > 1e-4: tris.append(Polygon(p))
    u = unary_union(tris).simplify(0.2)
    # holes filled: a part dropped into another part's bore or cavity collides with the supports grown in there (G-code conflicts)
    u = unary_union([Polygon(g.exterior) for g in getattr(u, 'geoms', [u])])
    return u.buffer(gap / 2, join_style=2)

def fits(fp, bed, margin, angles=range(0, 360, 15)):
    """True when the footprint fits an empty bed in one of the turns."""
    for a in angles:
        x0, y0, x1, y1 = rotate(fp, a, origin=(0, 0)).bounds
        if x1 - x0 <= bed - 2 * margin and y1 - y0 <= bed - 2 * margin: return True
    return False

def pack(fps, bed, margin, step=2.0, tries=12, angles=range(0, 360, 15), seconds=20):
    """fps: footprints centred on (0, 0). Returns [(plate index, x, y, angle)] — the part's centre on its plate, in mm.
    Stops trying new part orders after `seconds` and keeps the best layout found."""
    import random, time
    end = time.time() + seconds
    rnd = random.Random(1); order = sorted(range(len(fps)), key=lambda i: -fps[i].area); best = None
    for t in range(tries):
        if t and time.time() > end: break
        res, plates = _fill(fps, order, bed, margin, step, angles)
        score = (len(plates), sum(p.area for p in plates[-1]))
        if best is None or score < best[0]: best = (score, res)
        if len(plates) == 1: break
        order = order[:]; rnd.shuffle(order)
    return best[1]

def _fill(fps, order, bed, margin, step, angles):
    area = box(margin, margin, bed - margin, bed - margin); plates = []; out = [None] * len(fps)
    for i in order:
        for k in range(len(plates) + 1):
            if k == len(plates): plates.append([])
            placed = prep(unary_union(plates[k])) if plates[k] else None; best = None
            st = max(step, min(6.0, math.sqrt(fps[i].area) / 15))        # big parts: coarser steps
            for ang in (angles if fps[i].area > 800 else (0, 90)):       # small parts: two turns are enough
                f = rotate(fps[i], ang, origin=(0, 0)); x0, y0, x1, y1 = f.bounds
                if x1 - x0 > bed - 2 * margin or y1 - y0 > bed - 2 * margin: continue
                y = margin - y0
                while y + y1 <= bed - margin and (best is None or y < best[1]):
                    x = margin - x0
                    while x + x1 <= bed - margin:
                        g = translate(f, x, y)
                        if (placed is None or not placed.intersects(g)) and area.contains(g):
                            if best is None or (y, x) < (best[1], best[0]): best = (x, y, ang, g)
                            break
                        x += st
                    y += st
            if best:
                plates[k].append(best[3]); out[i] = (k, best[0], best[1], best[2]); break
            if not plates[k]: raise ValueError('part %d does not fit on an empty bed' % i)
    return out, plates
