"""Pure geometry helpers on tuples for rings, straight paths and gaps: bearing ring faces, the path in front of an opening,
the distance between boxes. Used by fusion_bearings, fusion_openings, fusion_screws and fusion_gap.

Like _geom.py this file is imported on the host and PASTED into Fusion scripts (after _geom.py, so the v_* helpers and
axis_frame are already globals there). Standard library only, no adsk, nothing runs at import except definitions.
Everything is in mm; points and vectors are tuples (x, y, z).

What "is this point inside that body" means is never decided here: a function that needs it takes a callable
(`inside(point) -> bool`, `owner(s) -> name or None`). In Fusion the probe passes BRepBody.pointContainment behind a
bounding-box test; in the tests it is a box or a tube written in three lines."""
import math

try:                                    # on the host; inside a Fusion script these names are already globals
    from probes._geom import v_add, v_sub, v_scale, v_dot, v_cross, v_len, v_unit, axis_frame, along, off_axis
except ImportError:
    try:
        from _geom import v_add, v_sub, v_scale, v_dot, v_cross, v_len, v_unit, axis_frame, along, off_axis
    except ImportError:
        pass


# ---- names ---------------------------------------------------------------------------------------------------------------------------

def name_slug(name):
    """The slug of gate_common.slug(), without `re`: lower case, every run of other characters becomes one underscore.
    A probe builds the part id of a printed neighbour with it: "<doc key>/<slug>"."""
    out, gap = [], False
    for c in str(name).lower():
        if ('a' <= c <= 'z') or ('0' <= c <= '9'):
            if gap and out:
                out.append('_')
            out.append(c); gap = False
        else:
            gap = True
    return ''.join(out) or 'part'


def body_label(component, body, n_solids):
    """How a body is named in facts: the component name, or "<component> / <body>" when the component has several solid
    bodies (the rule fusion_scan uses for parts)."""
    return component if n_solids <= 1 else '%s / %s' % (component, body)


def name_number_base(name):
    """A name without its copy number: "ref washer M3 (4)" -> "ref washer M3", "Washer:2" -> "Washer". A number that is
    part of the name ("screw M3x12") stays. Six washers round one ring are one cover with six contact runs."""
    s = str(name).rstrip()
    i, close = len(s), ''
    if i and s[-1] in ')]':
        close = s[-1]; i -= 1
    j = i
    while j > 0 and s[j - 1].isdigit():
        j -= 1
    if j == i or j == 0:
        return s
    opener = s[j - 1]
    if close:
        if opener != ('(' if close == ')' else '['):
            return s
    elif opener not in '#:':
        return s
    return s[:j - 1].rstrip() or s


def _rings_number_at(s, i):
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    if j == i:
        return None, i
    if j + 1 < len(s) and s[j] == '.' and s[j + 1].isdigit():
        j += 1
        while j < len(s) and s[j].isdigit():
            j += 1
    return s[i:j], j


def thread_in_name(name):
    """("M3", 12.0) from "ref screw M3x12 cap", ("M4", None) from "nut M4", (None, None) when the name has no thread."""
    s = str(name)
    for i, c in enumerate(s):
        if c not in 'Mm' or (i and s[i - 1].isalnum()):
            continue
        num, j = _rings_number_at(s, i + 1)
        if num is None:
            continue
        size = 'M' + (num[:-2] if num.endswith('.0') else num)
        k = j
        while k < len(s) and s[k] == ' ':
            k += 1
        if k < len(s) and s[k] in 'xX*':
            k += 1
            while k < len(s) and s[k] == ' ':
                k += 1
            ln, _ = _rings_number_at(s, k)
            if ln is not None:
                return size, float(ln)
        return size, None
    return None, None


def designation_in_name(name, sizes):
    """The bearing designation a name carries: a word that is a key of `sizes` ("6005"), also with a suffix that does not
    start with a digit ("6005ZZ", "6005-2RS"). The longest key wins. None when there is none."""
    words, cur = [], ''
    for c in str(name):
        if c.isalnum():
            cur += c
        elif cur:
            words.append(cur); cur = ''
    if cur:
        words.append(cur)
    for key in sorted(sizes, key=lambda k: (-len(k), k)):
        for w in words:
            if w == key or (w.startswith(key) and not w[len(key)].isdigit()):
                return key
    return None


def body_kind(printed, hardware, visible=True, mesh=False, own=False):
    """One word for what a body is to a check: "self" (the part of the unit), "mesh" (no B-rep: seen by its bounding box
    and vertices only), "hidden" (light bulb off), "printed", "hardware" (screw, washer, insert, nut, bearing) or
    "bought" (every other body that is not printed)."""
    if own:
        return 'self'
    if mesh:
        return 'mesh'
    if not visible:
        return 'hidden'
    if printed:
        return 'printed'
    return 'hardware' if hardware else 'bought'


def group_of(path, groups, search):
    """The key of the first motion group one of whose patterns matches `path`, or None.
    groups  [{"key": "slide", "patterns": [regex]}]        search  re.search (the caller owns `re`)"""
    for g in groups or []:
        if any(search(p, path) for p in g.get('patterns') or []):
            return g.get('key')
    return None


# ---- bodies of revolution, read from their circular edges -----------------------------------------------------------------------

def coaxial(o1, a1, o2, a2, tol=0.1, cos_tol=0.9998):
    """Are two lines the same axis: directions parallel (either way, about 1 degree) and `o2` within `tol` mm of line 1."""
    return abs(v_dot(a1, a2)) >= cos_tol and off_axis(o2, o1, a1) <= tol


def revolve_profile(circles, tol=0.05):
    """The outline of a turned body (a bearing, a screw, a washer, an insert) from its circular edges.

    circles  [(centre, normal, radius)]: every circle and arc edge of the body or bodies, mm. Arcs count too: an imported
             cylinder is often split into two half faces.
    tol      mm: a circle whose centre is this close to the axis is on the axis

    The axis is the line most circles share (ties: the one with the largest circle). Returns None with no circles, else
    {"origin": point on the axis, "axis": unit vector, "stations": [(s, r)] sorted, s along the axis from the origin,
     "lo": least s, "hi": largest s, "r_min", "r_max", "n": circles on the axis, "of": circles given}."""
    groups = []
    for c, nrm, r in sorted(circles, key=lambda t: -t[2]):
        a = v_unit(nrm)
        if v_len(a) < 0.5 or r <= 0:
            continue
        for g in groups:
            if coaxial(g['origin'], g['axis'], c, a, tol):
                g['items'].append((c, r)); break
        else:
            groups.append({'origin': c, 'axis': a, 'items': [(c, r)], 'r': r})
    if not groups:
        return None
    g = max(groups, key=lambda x: (len(x['items']), x['r']))
    st = sorted((round(along(c, g['origin'], g['axis']), 4), round(r, 4)) for c, r in g['items'])
    return {'origin': g['origin'], 'axis': g['axis'], 'stations': st, 'lo': st[0][0], 'hi': st[-1][0],
            'r_min': min(r for _, r in st), 'r_max': max(r for _, r in st), 'n': len(st), 'of': len(circles)}


def cluster_coaxial(items, slack=0.2):
    """Which turned bodies are one part: a screw drawn as a head body and a shank body, a bearing drawn as two rings.

    items  [(origin, axis, lo, hi)] of each body's revolve_profile()
    slack  mm: bodies on one axis whose stretches along it are no farther apart than this are one part. Negative: they
           must overlap by that much (two bearings side by side on one shaft stay two).

    Returns the groups as sorted lists of indices, in the order of their first index."""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i in range(len(items)):
        oi, ai, lo_i, hi_i = items[i]
        for j in range(i + 1, len(items)):
            oj, aj, lo_j, hi_j = items[j]
            if not coaxial(oi, ai, oj, aj):
                continue
            e = sorted((along(v_add(oj, v_scale(aj, lo_j)), oi, ai), along(v_add(oj, v_scale(aj, hi_j)), oi, ai)))
            if min(hi_i, e[1]) - max(lo_i, e[0]) >= -slack:
                parent[find(j)] = find(i)
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def bearing_size(profile):
    """Bore, outer diameter and width of a ring from its profile: {"d", "D", "B", "centre", "axis"}, or None when the
    profile is not a ring (no width, or no bore smaller than the outside)."""
    if not profile:
        return None
    B = profile['hi'] - profile['lo']
    d, D = 2 * profile['r_min'], 2 * profile['r_max']
    if B <= 1e-6 or d >= D - 1e-6:
        return None
    return {'d': d, 'D': D, 'B': B, 'axis': profile['axis'],
            'centre': v_add(profile['origin'], v_scale(profile['axis'], (profile['lo'] + profile['hi']) / 2.0))}


def match_bearing(d, D, B, table):
    """The designation of rules/tables/bearings.json whose d, D and B all lie within the table's tolerance, or None."""
    tol = float((table.get('tol') or {}).get('d', 0.05))
    for key in sorted(table.get('sizes') or {}):
        row = table['sizes'][key]
        if abs(d - row['d']) <= tol and abs(D - row['D']) <= tol and abs(B - row['B']) <= tol:
            return key
    return None


def screw_shape(profile):
    """Head, shank and ends of a screw from its profile.

    Returns None when the profile has no length, else
    {"axis": unit vector from the tip to the head, "tip": point, "seat": point (centre of the face under the head; for a
     countersunk head the start of the cone), "top": point (top of the head), "head_r", "shank_r", "head_h",
     "length": mm as a screw is named (under the head; a countersunk screw with its head),
     "countersunk": bool, "headless": bool (a set screw: no head wider than the shank)}."""
    if not profile or profile['hi'] - profile['lo'] <= 1e-6:
        return None
    st, lo, hi, r_h = profile['stations'], profile['lo'], profile['hi'], profile['r_max']
    wide = [s for s, r in st if r >= r_h - 0.05]
    sign = 1.0 if (sum(wide) / len(wide)) - lo >= hi - (sum(wide) / len(wide)) else -1.0     # the head is the wide end
    pts = sorted((sign * s, r) for s, r in st)                                                 # from the tip to the head
    s_tip, s_top = pts[0][0], pts[-1][0]
    spans = {}
    for s, r in pts:
        if r < 0.8 * r_h:
            k = round(r / 0.02)
            spans[k] = (min(spans.get(k, (s, s))[0], s), max(spans.get(k, (s, s))[1], s), r)
    P = lambda s: v_add(profile['origin'], v_scale(profile['axis'], sign * s))
    axis = v_scale(profile['axis'], sign)
    if not spans:                                                                              # nothing narrower: no head
        return {'axis': axis, 'tip': P(s_tip), 'seat': P(s_top), 'top': P(s_top), 'head_r': r_h, 'shank_r': r_h, 'head_h': 0.0,
                'length': s_top - s_tip, 'countersunk': False, 'headless': True}
    r_s = max(spans.values(), key=lambda t: (round(t[1] - t[0], 3), t[2]))[2]
    s_big = min(s for s, r in pts if r >= r_s + 0.6 * (r_h - r_s))                             # the head has begun
    s_c = max([s for s, r in pts if r <= r_s + 0.15 and s <= s_big + 1e-6] or [s_tip])         # the shank ends
    s_full = min(s for s, r in pts if r >= r_h - 0.1 * (r_h - r_s))                            # the head is at its full width
    flat = (s_full - s_c) <= max(0.3, 0.15 * (r_h - r_s))
    return {'axis': axis, 'tip': P(s_tip), 'seat': P(s_c), 'top': P(s_top), 'head_r': r_h, 'shank_r': r_s,
            'head_h': s_top - s_c, 'length': (s_c - s_tip) if flat else (s_top - s_tip), 'countersunk': not flat, 'headless': False}


def screw_size(shape, table, tol_d=0.35):
    """Thread size and head kind of a screw_shape() against rules/tables/screws.json.
    Returns (size or None, head or None): size = the row whose thread_d is nearest the shank diameter within tol_d (a
    modelled thread is often drawn at its core diameter); head = "countersunk", or "cap" / "button", whichever head of
    that row is nearer in diameter and height, within 0.6 mm; None for a set screw or a head the table does not know."""
    sizes = table.get('sizes') or {}
    best = None
    for key in sorted(sizes):
        e = abs(sizes[key]['thread_d'] - 2 * shape['shank_r'])
        if e <= tol_d and (best is None or e < best[0]):
            best = (e, key)
    if best is None:
        return None, None
    size = best[1]
    if shape['headless']:
        return size, None
    if shape['countersunk']:
        return size, 'countersunk'
    head = None
    for kind in ('cap', 'button'):
        row = (sizes[size].get('heads') or {}).get(kind)
        if not row:
            continue
        e = max(abs(row['d'] - 2 * shape['head_r']), abs(row['h'] - shape['head_h']))
        if e <= 0.6 and (head is None or e < head[0]):
            head = (e, kind)
    return size, (head[1] if head else None)


def washer_shape(profile):
    """A flat washer from its profile: {"od", "id", "t", "centre", "axis"}, or None when it is not a thin ring."""
    b = bearing_size(profile)
    if b is None or b['B'] >= b['D'] / 2.0:
        return None
    return {'od': b['D'], 'id': b['d'], 't': b['B'], 'centre': b['centre'], 'axis': b['axis']}


def washer_row(shape, table):
    """(size, kind) of the row of rules/tables/washers.json a washer_shape() is ("M3", "large"), or None: inner
    diameter and outer diameter within the table's "d" tolerance (also of a row's "od_measured"), thickness within "t"."""
    tol = (table or {}).get('tol') or {}
    td, tt = float(tol.get('d', 0.3)), float(tol.get('t', 0.2))
    sizes = (table or {}).get('sizes') or {}
    for size in sorted(sizes):
        for kind in sorted(sizes[size]):
            row = sizes[size][kind]
            if not isinstance(row, dict) or 'od' not in row:
                continue
            ods = [row['od']] + ([row['od_measured']] if 'od_measured' in row else [])
            if abs(shape['id'] - row['id']) <= td and min(abs(shape['od'] - o) for o in ods) <= td and abs(shape['t'] - row['t']) <= tt:
                return size, kind
    return None


# ---- a name is a hint, the geometry decides ------------------------------------------------------------------------------------

def screw_claim(shape, table, named_size, own_name):
    """Is a body that a name picked really a screw? Returns (ok, why not or None, size, head).

    shape       screw_shape() of the body or bodies, or None
    table       rules/tables/screws.json
    named_size  the thread its name carries ("M3"), or None
    own_name    True when the body's OWN name says screw (the screw pattern matches the body name). False when only
                the name of its component does: a component "hardware (screws, magnets)" holds other things too.

    The rules:
      - a name that carries a thread of the table and a shank of another diameter: not that screw;
      - picked by the component name alone: it needs a head on a shank of a table thread. A plain cylinder there is
        a magnet, a pin or a spacer as likely as a set screw;
      - picked by its own name: a set screw (no head) and a thread the table does not hold are kept, and so is a body
        with no readable shape (the caller lists it with "why")."""
    sizes = (table or {}).get('sizes') or {}
    if shape is None:
        return (True, None, named_size, None) if own_name else (False, 'no coaxial circular edges, and its own name does not say screw', None, None)
    size, head = screw_size(shape, table or {})
    d, length = 2.0 * shape['shank_r'], shape['length']
    if named_size in sizes and size != named_size:
        return False, 'its name says %s, its shank measures D%s' % (named_size, _rings_g(d)), size, head
    if not own_name:
        if size is None:
            return False, 'D%s x %s is no thread of the screws table, and its own name does not say screw' % (_rings_g(d), _rings_g(length)), None, None
        if shape['headless']:
            return False, 'a plain cylinder D%s x %s with no head, and its own name does not say screw' % (_rings_g(d), _rings_g(length)), size, None
    return True, None, size or named_size, head


def bearing_claim(size, named, table):
    """Is a body that a name picked really a bearing? Returns (designation or None, "geometry" | "name" | None, why not or None).

    size   bearing_size() of the body or bodies, or None          named  the designation its name carries, or None
    table  rules/tables/bearings.json

    A ring whose d, D and B are a row of the table is that bearing, whatever it is called. A name alone stands only
    while there is no ring geometry to hold it against. A ring that measures otherwise than the row its name carries is
    NOT that bearing: (None, None, why)."""
    if size:
        hit = match_bearing(size['d'], size['D'], size['B'], table or {})
        if hit:
            return hit, 'geometry', None
        row = ((table or {}).get('sizes') or {}).get(named or '')
        if row:
            return None, None, 'its name says %s (%s x %s x %s), it measures %s x %s x %s' % (
                named, _rings_g(row['d']), _rings_g(row['D']), _rings_g(row['B']), _rings_g(size['d']), _rings_g(size['D']), _rings_g(size['B']))
        return None, None, None
    return (named, 'name', None) if named else (None, None, None)


def _rings_g(x):
    return '%g' % round(float(x), 2)


# ---- ring faces ------------------------------------------------------------------------------------------------------------------

def ring_annulus(d, D, ring, land_share=0.25):
    """(r_in, r_out) of the flat face of one bearing ring.

    d, D        bore and outer diameter of the bearing, mm
    ring        "inner" | "outer"
    land_share  radial width of one ring face as a share of (D - d) / 2 (rules/tables/bearings.json "ring_land_share")

    inner: from d / 2 outwards; outer: up to D / 2 inwards."""
    land = land_share * (D - d) / 2.0
    if ring == 'inner':
        return (d / 2.0, d / 2.0 + land)
    if ring == 'outer':
        return (D / 2.0 - land, D / 2.0)
    raise ValueError('ring must be "inner" or "outer", got %r' % (ring,))


def ring_face_centre(centre, axis, B, side):
    """Centre of one face of a bearing: `centre` moved by B / 2 along the unit `axis` ("+") or against it ("-")."""
    if side not in ('+', '-'):
        raise ValueError('side must be "+" or "-", got %r' % (side,))
    return v_add(centre, v_scale(axis, (B / 2.0) if side == '+' else (-B / 2.0)))


def annulus_points(centre, axis, r_in, r_out, n_ang=24, n_rad=2):
    """Sample points on an annulus: n_rad circles between r_in and r_out (not on the edges), n_ang points on each.
    Returns [(point, angle index, circle index)] in a stable order (circle by circle, angle 0 first)."""
    u, v = axis_frame(axis)
    out = []
    for j in range(n_rad):
        r = r_in + (r_out - r_in) * (j + 1) / (n_rad + 1.0)
        for k in range(n_ang):
            th = 2 * math.pi * k / n_ang
            out.append((v_add(centre, v_add(v_scale(u, r * math.cos(th)), v_scale(v, r * math.sin(th)))), k, j))
    return out


def ring_angles(r, arc=3.0, least=24, most=144):
    """How many points one circle of radius r mm gets: one every `arc` mm, never fewer than `least` (the count small
    rings always had) and never more than `most`. 24 points on a D100 ring stand 13 mm apart, and a D12 clamp washer
    that reaches 2 mm over the ring falls between them."""
    return int(max(least, min(most, math.ceil(2.0 * math.pi * max(0.0, float(r)) / arc))))


def arc_runs(flags):
    """What a ring of yes / no samples says about a cover. `flags` is a list of bool round one circle (closed).
    Returns (share, runs): the share of True, and the number of separate True runs round the circle (a full circle is 1
    run; six washers are 6 runs)."""
    n = len(flags)
    if not n:
        return 0.0, 0
    hits = sum(1 for f in flags if f)
    if hits == n:
        return 1.0, 1
    return hits / float(n), sum(1 for i in range(n) if flags[i] and not flags[i - 1])


def cover_offsets(touch, look, step=0.1):
    """The distances from a face at which a cover is looked for: 0.02 (touching), touch + 0.02 (inside the contact gap),
    then every `step` mm out to `look`. face_cover() reports the first one that is inside the cover."""
    out = [0.02, round(touch + 0.02, 4)]
    s = out[-1] + step
    while s <= look + 0.02 + 1e-9:
        out.append(round(s, 4)); s += step
    return out


def face_first(points, inside, direction, offsets):
    """Where a body begins in front of each sample point of a face: [index into `offsets` of the first offset at which
    the point, pushed out of the face along `direction`, is inside the body; None when it is at none of them].
    One entry per point of `points` (annulus_points), in their order. This is the costly half of face_cover(): one
    call of `inside` per point and offset until the first hit."""
    first = []
    for p, _, _ in points:
        hit = None
        for i, o in enumerate(offsets):
            if inside(v_add(p, v_scale(direction, o))):
                hit = i; break
        first.append(hit)
    return first


def face_first_cut(first, n):
    """face_first() as it would have come out with the first n offsets only: a hit further out is no hit."""
    return [i if (i is not None and i < n) else None for i in first]


def face_cover_of(points, first, offsets, touch):
    """The cover of a face from its face_first() list: what face_cover() returns."""
    n_ang = 1 + max(k for _, k, _ in points)
    seen = [offsets[i] for i in first if i is not None]
    if not seen:
        return None
    flags = [False] * n_ang
    for (p, k, _), i in zip(points, first):
        if i is not None and offsets[i] <= touch + 0.02 + 1e-9:
            flags[k] = True
    close = sum(1 for o in seen if o <= touch + 0.02 + 1e-9)
    return {'share': round(close / float(len(points)), 3), 'points': arc_runs(flags)[1] if close else 0,
            'gap': round(max(0.0, min(seen) - 0.02), 3), 'near': round(len(seen) / float(len(points)), 3), 'flags': flags}


def face_cover(points, inside, direction, offsets, touch):
    """How one body covers a flat ring face.

    points     annulus_points() of the face
    inside     callable(point) -> bool: is the point inside the body
    direction  unit vector out of the face (away from the ring)
    offsets    cover_offsets(): where to look, nearest first
    touch      mm: a cover this close lies against the face (bearings.contact_gap_max, washers.landing_gap_max)

    Returns None when the body is nowhere within the last offset, else
    {"share": share of the sample points with the body within `touch`, "points": separate contact runs round the circle
     (arc_runs over the angles), "gap": mm from the face to the body at its nearest sample (0.0 = it touches),
     "near": share of the sample points with the body within the last offset, "flags": [bool per angle] within `touch`}.
    face_first() asks the body, face_cover_of() counts: a caller that needs the same points twice asks once."""
    return face_cover_of(points, face_first(points, inside, direction, offsets), offsets, touch)


def slab_box(points, direction, offsets):
    """The bounding box of every place face_first() looks at: the sample points pushed out by the first and by the
    last offset. A body whose own box does not reach this box is at none of them."""
    ends = [v_add(p, v_scale(direction, o)) for p, _, _ in points for o in (offsets[0], offsets[-1])]
    return box_of(ends)


def landing_band(points, hit, r_in, r_out):
    """Where a washer lies on its landing: (share, open_d, [share per circle, innermost first]).

    points       annulus_points(centre, axis, r_in, r_out, n_ang, n_rad) of the washer's landing face
    hit          callable(point) -> bool: material lies under the point within the contact gap
    r_in, r_out  the radii the points were made with

    The landing band is counted from the rim inwards while at least half of a circle is in contact. The circles inside
    it are the opening the washer sits over (its screw hole, a pocket mouth, a melt relief): a washer that bridges a
    relief and lands on the face round it has a full band. share = the mean of the band's circles; with no band (the
    rim circle itself is not in contact: the washer hangs in air or over an edge) it is the mean of all circles.
    open_d = twice the radius of the widest circle inside the band, None when the band reaches the innermost circle."""
    rings = {}
    for p, _, j in points:
        rings.setdefault(j, []).append(bool(hit(p)))
    order = sorted(rings)
    shares = [sum(rings[j]) / float(len(rings[j])) for j in order]
    if not shares:
        return 0.0, None, []
    band = []
    for i in range(len(shares) - 1, -1, -1):
        if shares[i] < 0.5:
            break
        band.append(i)
    rounded = [round(x, 3) for x in shares]
    if not band:
        return round(sum(shares) / len(shares), 3), None, rounded
    inner, n = min(band), len(shares)
    open_d = None if inner == 0 else round(2.0 * (r_in + (r_out - r_in) * inner / (n + 1.0)), 2)
    return round(sum(shares[i] for i in band) / len(band), 3), open_d, rounded


def face_overlap(points, inside, direction, depths=(0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0), limit=8):
    """How far a cover reaches INTO the ring behind a face (a clamp drawn with its preload): the deepest of `depths` at
    which, going in from the face, the body is still there without a break. 0.0 = it stops at the face. Looks at no more
    than `limit` sample points, spread round the circle."""
    step = max(1, len(points) // max(1, limit))
    deep = 0.0
    for p, _, _ in points[::step][:limit]:
        for dp in depths:
            if not inside(v_sub(p, v_scale(direction, dp))):
                break
            deep = max(deep, dp)
    return deep


def clamp_room(ring_width, travel, gap_stop=0.0, gap_clamp=0.0):
    """Axial room between the two stops of a clamped ring, mm.

    ring_width  B of the bearing            travel     how far the clamp can still move towards the ring before it lands
    gap_stop    air between the ring and the stop on the far face, as drawn (negative: drawn overlapping)
    gap_clamp   air between the ring and the clamp, as drawn (negative: drawn overlapping)

    A ring is clamped only when the room is shorter than the ring: the difference is taken up by the screws."""
    return round(ring_width + gap_stop + gap_clamp - travel, 3)


# ---- sampling along one line -------------------------------------------------------------------------------------------------------

def _rings_last_true(pred, a, b, tol):
    while b - a > tol:                  # pred(a) is True, pred(b) is False
        m = (a + b) / 2.0
        if pred(m):
            a = m
        else:
            b = m
    return a


def _rings_first_true(pred, a, b, tol):
    while b - a > tol:                  # pred(a) is False, pred(b) is True
        m = (a + b) / 2.0
        if pred(m):
            b = m
        else:
            a = m
    return b


def runs_along(owner, s0, s1, step=0.25, tol=0.02):
    """What a straight line passes through.

    owner   callable(s) -> a name (the body that holds the point at distance s) or None (air)
    s0, s1  the stretch, s0 <= s1           step  spacing of the first pass           tol  how exactly the ends are found

    Returns [(name, s_from, s_to)]: one entry per stretch of material, in order; air is what lies between them. The ends
    are refined by halving, so a 0.3 mm gap between two bodies is measured even when no sample fell into it. A body
    thinner than `step` that lies between two samples is not seen."""
    if s1 < s0:
        raise ValueError('runs_along needs s0 <= s1')
    n = max(1, int(math.ceil((s1 - s0) / float(step) - 1e-9)))
    ss = [s0 + (s1 - s0) * i / float(n) for i in range(n + 1)]
    names = [owner(s) for s in ss]
    runs, cur, start = [], names[0], ss[0]
    for i in range(1, n + 1):
        nxt = names[i]
        if nxt == cur:
            continue
        a, b = ss[i - 1], ss[i]
        if cur is not None:
            runs.append((cur, start, _rings_last_true(lambda s, c=cur: owner(s) == c, a, b, tol)))
        if nxt is not None:
            start = _rings_first_true(lambda s, c=nxt: owner(s) == c, a, b, tol)
        cur = nxt
    if cur is not None:
        runs.append((cur, start, ss[-1]))
    return runs


def run_gaps(runs):
    """The air between consecutive stretches of runs_along(): [(gap, name before, name after, s where the gap starts)].
    A negative gap means the two bodies are drawn overlapping there."""
    return [(runs[i + 1][1] - runs[i][2], runs[i][0], runs[i + 1][0], runs[i][2]) for i in range(len(runs) - 1)]


def first_hit(owner, s0, s1, step=0.25, tol=0.02):
    """The first material on a line: (name, s) or None when the stretch s0 .. s1 is air. Stops at the first sample that
    is inside something, so a line that starts in contact costs one call."""
    if owner(s0) is not None:
        return owner(s0), s0
    n = max(1, int(math.ceil((s1 - s0) / float(step) - 1e-9)))
    prev = s0
    for i in range(1, n + 1):
        s = s0 + (s1 - s0) * i / float(n)
        name = owner(s)
        if name is not None:
            return name, _rings_first_true(lambda x: owner(x) is not None, prev, s, tol)
        prev = s
    return None


def least_travel(hits, look):
    """Bookkeeping for a clamp: `hits` is [(travel mm, name of what stops it)] from many lines, None entries are lines
    that met nothing. Returns (travel, name, capped): the least travel and its stop, or (look, None, True) when nothing
    stops the clamp within `look` mm."""
    best = None
    for h in hits:
        if h is not None and (best is None or h[0] < best[0]):
            best = h
    if best is None or best[0] >= look:
        return look, None, True
    return max(0.0, best[0]), best[1], False


# ---- the radial seat of a ring -----------------------------------------------------------------------------------------------------

def radial_edge(inside, centre, axis, s, direction, r_air, r_mat, tol=0.01):
    """The radius at which a body begins, on the line from the axis outwards in `direction` at height s.
    r_air is a radius expected in air, r_mat one expected in material (r_mat > r_air: a hole wall; r_mat < r_air: a shaft).
    Returns None when the pattern is not there (air at both, material at both, or the other way round)."""
    base = v_add(centre, v_scale(axis, s))
    at = lambda r: inside(v_add(base, v_scale(direction, r)))
    if at(r_air) or not at(r_mat):
        return None
    a, b = r_air, r_mat
    while abs(b - a) > tol:
        m = (a + b) / 2.0
        if at(m):
            b = m
        else:
            a = m
    return (a + b) / 2.0


def _rings_seat_frame(axis, r_nom, outer, window, n_ang):
    """(window as used, the n_ang directions round the axis, radius expected in air, radius expected in material) of a
    seat_scan()."""
    window = min(window, r_nom / 2.0)
    u, v = axis_frame(axis)
    dirs = [v_add(v_scale(u, math.cos(2 * math.pi * k / n_ang)), v_scale(v, math.sin(2 * math.pi * k / n_ang))) for k in range(n_ang)]
    r_air, r_mat = (r_nom - window, r_nom + window) if outer else (r_nom + window, r_nom - window)
    return window, dirs, r_air, r_mat


def seat_possible(bmin, bmax, centre, axis, r_nom, outer=True, window=1.0, n_ang=8):
    """Can a body with this bounding box be a seat for seat_scan() at all? A seat needs a wall in three quarters of the
    directions at mid width, and a wall needs the point at the radius expected in material to be inside the body, so
    inside its box. False = seat_scan() returns None for every body with that box, and no point need be asked."""
    _, dirs, _, r_mat = _rings_seat_frame(axis, r_nom, outer, window, n_ang)
    held = 0
    for d in dirs:
        p = v_add(centre, v_scale(d, r_mat))
        if all(bmin[i] <= p[i] <= bmax[i] for i in range(3)):
            held += 1
    return held >= 0.75 * n_ang


def seat_scan(inside, centre, axis, r_nom, width, outer=True, window=1.0, look_in=3.0, n_ang=8, entry_len=None, entry_step=1.0):
    """Does a body hold a ring radially, and can the ring get to its seat.

    inside   callable(point) -> bool for ONE body (the candidate seat)
    centre   centre of the bearing          axis  its unit axis          width  B
    r_nom    D / 2 for the outer ring (outer=True: the body is a hole round the ring), d / 2 for the inner ring
             (outer=False: the body is a shaft or post inside the ring)
    window   mm: a wall within r_nom +- window at mid width is a seat (its real diameter is measured)
    look_in  mm: how far into the ring's way a narrowing is followed on the way in
    entry_len, entry_step   the stretch outside each face that the ring must pass (default: the width, 6 .. 20 mm)

    Returns None when the body is no seat (fewer than three quarters of the directions show a wall), else
    {"d": seat diameter at the ring (median), "len": mm of the width over which the wall is there (in ninths of B),
     "entry_d": the narrowest (outer) or widest (inner) diameter of this body on the more open side,
     "entry_side": "+" | "-", "entry_open": bool (nothing of this body stands in the way on that side: entry_d is then
     the edge of the window, not a measured wall), "entry_other": the same diameter on the other side,
     "rows": the seat diameter (median of the directions) at each of the nine heights, from -0.4 B to +0.4 B along the
     axis, None where fewer than half of the directions show a wall: the profile of a tapered seat. The same points
     as "d": no point test more}."""
    window, dirs, r_air, r_mat = _rings_seat_frame(axis, r_nom, outer, window, n_ang)
    mid = [radial_edge(inside, centre, axis, 0.0, d, r_air, r_mat) for d in dirs]
    if sum(1 for r in mid if r is not None) < 0.75 * n_ang:
        return None
    radii, held, rows = [], 0, []
    for i in range(9):
        s = width * (-0.4 + 0.1 * i)
        row = mid if i == 4 else [radial_edge(inside, centre, axis, s, d, r_air, r_mat) for d in dirs]
        found = [r for r in row if r is not None]
        radii += found
        if len(found) >= 0.5 * n_ang:
            held += 1
        found.sort()
        rows.append(round(2 * found[len(found) // 2], 2) if len(found) >= 0.5 * n_ang else None)
    radii.sort()
    L = entry_len if entry_len is not None else min(20.0, max(width, 6.0))
    n_st = max(1, int(math.ceil(L / entry_step)))
    r_deep = (r_nom - look_in) if outer else (r_nom + look_in)       # as far into the ring's way as we look
    if outer:
        r_deep = max(r_deep, 0.2)
    sides = {}
    for side, sgn in (('+', 1.0), ('-', -1.0)):
        worst = None                                                  # the narrowest (outer) / widest (inner) radius met
        for i in range(n_st):
            s = sgn * (width / 2.0 + entry_step * (i + 0.5))
            base = v_add(centre, v_scale(axis, s))
            for d in dirs:
                if inside(v_add(base, v_scale(d, r_deep))):
                    r = r_deep
                else:
                    r = radial_edge(inside, centre, axis, s, d, r_deep, r_mat)
                if r is not None and (worst is None or (r < worst if outer else r > worst)):
                    worst = r
        sides[side] = worst
    edge = r_mat                                                      # "open": nothing of this body inside the window
    val = lambda side: edge if sides[side] is None else sides[side]
    best = max(('+', '-'), key=lambda sd: (val(sd) if outer else -val(sd), sd == '+'))
    other = '-' if best == '+' else '+'
    return {'d': round(2 * radii[len(radii) // 2], 2), 'len': round(width * held / 9.0, 2),
            'entry_d': round(2 * val(best), 2), 'entry_side': best, 'entry_open': sides[best] is None,
            'entry_other': round(2 * val(other), 2), 'rows': rows}


def seat_has_bearing(point, axis, depth, bearings, tol=0.5):
    """Is one of `bearings` ([(centre, axis)], same frame as the hole) in a census hole: on its axis within `tol` and
    between 1 mm outside the mouth and 1 mm behind the hole's depth.
    point, axis  the hole's anchor (mouth centre, axis out of the material)          depth  of the hole"""
    for c, a in bearings:
        if coaxial(point, axis, c, a, tol) and -depth - 1.0 <= along(c, point, axis) <= 1.0:
            return True
    return False


# ---- the straight path in front of an opening ----------------------------------------------------------------------------------

def path_points(point, axis, r, reach, step=2.0, n_ang=8):
    """Sample points of a straight cylinder of radius `r` that starts at `point` and runs `reach` mm along the unit `axis`:
    the centre line and one circle at 0.9 r, every `step` mm. Returns [(point, s)] with s the distance from the start."""
    u, v = axis_frame(axis)
    n = int(math.floor(reach / float(step) + 1e-9))
    ss = [i * step for i in range(n + 1)]
    if reach - ss[-1] > 1e-9:
        ss.append(reach)
    out = []
    for s in ss:
        c = v_add(point, v_scale(axis, s))
        out.append((c, s))
        for k in range(n_ang):
            th = 2 * math.pi * k / n_ang
            out.append((v_add(c, v_add(v_scale(u, 0.9 * r * math.cos(th)), v_scale(v, 0.9 * r * math.sin(th)))), s))
    return out


def path_box(point, axis, r, reach):
    """The axis-aligned box (min, max) round that straight cylinder: exact, not padded."""
    end = v_add(point, v_scale(axis, reach))
    pad = [r * math.sqrt(max(0.0, 1.0 - axis[i] * axis[i])) for i in range(3)]
    return (tuple(min(point[i], end[i]) - pad[i] for i in range(3)), tuple(max(point[i], end[i]) + pad[i] for i in range(3)))


def box_span(bmin, bmax, point, axis):
    """(lo, hi): the stretch of the line through `point` along `axis` that an axis-aligned box covers (its eight corners)."""
    vals = [along((x, y, z), point, axis) for x in (bmin[0], bmax[0]) for y in (bmin[1], bmax[1]) for z in (bmin[2], bmax[2])]
    return min(vals), max(vals)


def box_overlaps_path(bmin, bmax, point, axis, r, reach):
    """Can an axis-aligned box touch that straight cylinder? Conservative: True when unsure. For skipping far bodies."""
    pmin, pmax = path_box(point, axis, r, reach)
    if any(bmin[i] > pmax[i] or bmax[i] < pmin[i] for i in range(3)):
        return False
    lo, hi = box_span(bmin, bmax, point, axis)
    if hi < 0.0 or lo > reach:
        return False
    centre = tuple((bmin[i] + bmax[i]) / 2.0 for i in range(3))
    half_diag = v_len(v_sub(bmax, bmin)) / 2.0
    return off_axis(centre, point, axis) - half_diag <= r


def points_in_path(points, point, axis, r, reach):
    """Which of `points` lie inside that straight cylinder. Returns [(s, off, index)]: distance along the axis, distance
    from the axis, and the index in `points`, sorted by s. Used for mesh refs (vertices) after the box test."""
    out = []
    for i, p in enumerate(points):
        s = along(p, point, axis)
        if s < 0.0 or s > reach:
            continue
        off = off_axis(p, point, axis)
        if off <= r:
            out.append((s, off, i))
    out.sort()
    return out


def section_share(n_hit, n_all):
    """Share of a path section that is blocked, from the count of blocked sample points at one distance: n_hit / n_all,
    0.0 when n_all is 0."""
    return (n_hit / float(n_all)) if n_all else 0.0


def path_share(inside, point, axis, r, s_from, s_to, n_stations=12, n_ang=8):
    """The largest blocked share of the path's section between two distances: at up to n_stations distances the centre
    and two circles (0.5 r and 0.9 r, n_ang points each) are asked `inside`; the fullest section counts."""
    u, v = axis_frame(axis)
    n = max(1, n_stations)
    best = 0.0
    for i in range(n):
        s = s_from + (s_to - s_from) * (i + 0.5) / n
        c = v_add(point, v_scale(axis, s))
        pts = [c]
        for f in (0.5, 0.9):
            for k in range(n_ang):
                th = 2 * math.pi * (k + (0.5 if f == 0.5 else 0.0)) / n_ang
                pts.append(v_add(c, v_add(v_scale(u, f * r * math.cos(th)), v_scale(v, f * r * math.sin(th)))))
        best = max(best, section_share(sum(1 for p in pts if inside(p)), len(pts)))
        if best >= 1.0:
            break
    return best


def path_free(hits, reach):
    """Free length of a path: the distance to the nearest hit that is not expected, `reach` when there is none.
    hits  [{"at": mm, "expected": bool, ...}]"""
    ats = [h['at'] for h in hits if not h.get('expected')]
    return round(min([reach] + ats), 2)


def mesh_blocks_path(nodes, tris, point, axis, r, reach, n_ang=8):
    """A mesh body against that straight cylinder, without a B-rep: approximate.

    nodes  [(x, y, z)] of the mesh in the frame of the path          tris  [(i, j, k)] indices into nodes

    First the vertices inside the path, then, for a mesh of big flat triangles that has none there, the triangles cut by
    nine lines (the centre line and eight at 0.9 r). Returns None when nothing is met, else (at, share): the distance of
    the first contact and a coarse share of the section (sixteenths from vertices, ninths from lines)."""
    inside = points_in_path(nodes, point, axis, r, reach)
    if inside:
        at = inside[0][0]
        u, v = axis_frame(axis)
        cells = set()
        for s, off, i in inside:
            if s > at + 2.0:
                break
            d = v_sub(nodes[i], point)
            ang = math.atan2(v_dot(d, v), v_dot(d, u)) % (2 * math.pi)
            cells.add((int(ang / (2 * math.pi) * 8) % 8, 0 if off < 0.5 * r else 1))
        return round(at, 2), round(len(cells) / 16.0, 3)
    pmin, pmax = path_box(point, axis, r, reach)
    u, v = axis_frame(axis)
    starts = [point] + [v_add(point, v_add(v_scale(u, 0.9 * r * math.cos(2 * math.pi * k / n_ang)),
                                           v_scale(v, 0.9 * r * math.sin(2 * math.pi * k / n_ang)))) for k in range(n_ang)]
    first = [None] * len(starts)
    for i, j, k in tris:
        a, b, c = nodes[i], nodes[j], nodes[k]
        if any((a[m] < pmin[m] and b[m] < pmin[m] and c[m] < pmin[m]) or (a[m] > pmax[m] and b[m] > pmax[m] and c[m] > pmax[m]) for m in range(3)):
            continue
        e1, e2 = v_sub(b, a), v_sub(c, a)
        h = v_cross(axis, e2)
        det = v_dot(e1, h)
        if abs(det) < 1e-12:
            continue
        for n, o in enumerate(starts):
            sv = v_sub(o, a)
            bu = v_dot(sv, h) / det
            if bu < 0.0 or bu > 1.0:
                continue
            q = v_cross(sv, e1)
            bv = v_dot(axis, q) / det
            if bv < 0.0 or bu + bv > 1.0:
                continue
            t = v_dot(e2, q) / det
            if 0.0 <= t <= reach and (first[n] is None or t < first[n]):
                first[n] = t
    met = [t for t in first if t is not None]
    if not met:
        return None
    return round(min(met), 2), round(len(met) / float(len(starts)), 3)


# ---- gaps ---------------------------------------------------------------------------------------------------------------------------

def box_gap(a_min, a_max, b_min, b_max):
    """Distance between two axis-aligned boxes in mm; 0.0 when they touch or overlap."""
    d = [max(0.0, a_min[i] - b_max[i], b_min[i] - a_max[i]) for i in range(3)]
    return math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])


def box_of(points):
    """(min, max) of a list of points: the box of a body after its corners were moved into another frame."""
    return (tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3)))


def box_corners(bmin, bmax):
    """The eight corners of an axis-aligned box."""
    return [(x, y, z) for x in (bmin[0], bmax[0]) for y in (bmin[1], bmax[1]) for z in (bmin[2], bmax[2])]


def box_nearest(bmin, bmax, p):
    """The point of an axis-aligned box nearest to `p` (p itself when it is inside)."""
    return tuple(min(max(p[i], bmin[i]), bmax[i]) for i in range(3))


def least_gaps(rows, margin):
    """Bookkeeping for the gap list: of rows ({"name", "gap", ...}) keep those within `margin`, one per name and group
    (the least gap), nearest first, then by name. Rows are not changed."""
    best = {}
    for r in rows:
        if r['gap'] > margin:
            continue
        k = (r.get('name'), r.get('group'))
        if k not in best or r['gap'] < best[k]['gap']:
            best[k] = r
    return sorted(best.values(), key=lambda r: (r['gap'], str(r.get('name')), str(r.get('group'))))
