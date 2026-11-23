"""Heat-set gate, the deterministic part: no Fusion here. Pure Python (3.9+), so the same text runs inside Fusion (pasted
in front of heatset_fusion.py) and in the tests.

Data in (from heatset_fusion.py, all mm, world frame):
  a cylinder   {"d", "o": [x,y,z] a point on the axis, "a": [ax,ay,az] unit axis, "t0", "t1" extent along a from o,
                "concave": True for a hole face, "span": degrees of the circle the face covers}
  a part       {"name", "cylinders": [...], "groups": [...] after group_holes() + the open ends Fusion measured}
  an insert    {"name", "path", "cylinders": [...], "volume"}

What comes out: findings, each {"part", "code", "at": [x,y,z] the pocket mouth, "out": [ax,ay,az] the axis out of the
material, "measured", "expected", "fix"}; "at" + "out" are what tools/fusion/insert_pockets.py cut() and relief() take."""
import math, re

EPS_T = 0.02          # mm: two segments closer than this along the axis touch


# ---------------------------------------------------------------- vectors
def _dot(u, v): return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
def _sub(u, v): return [u[0] - v[0], u[1] - v[1], u[2] - v[2]]
def _add(u, v): return [u[0] + v[0], u[1] + v[1], u[2] + v[2]]
def _mul(u, s): return [u[0] * s, u[1] * s, u[2] * s]
def _norm(u):
    n = math.sqrt(_dot(u, u)) or 1.0
    return [u[0] / n, u[1] / n, u[2] / n]


def canon(o, a):
    """(unit axis with a fixed sign, the point of the axis line closest to the world origin)."""
    a = _norm(a)
    for c in a:
        if abs(c) > 1e-6:
            if c < 0: a = [-a[0], -a[1], -a[2]]
            break
    base = _sub(o, _mul(a, _dot(o, a)))
    return a, base


def line_distance(a1, b1, a2, b2):
    """Distance between two parallel lines (axis a1 through b1, axis a2 through b2); None when they are not parallel."""
    if abs(abs(_dot(a1, a2)) - 1) > 1e-6:
        return None
    w = _sub(b2, b1)
    w = _sub(w, _mul(a1, _dot(w, a1)))
    return math.sqrt(_dot(w, w))


def _ld(a1, b1, a2, b2):
    d = line_distance(a1, b1, a2, b2)
    return 1e9 if d is None else d


# ---------------------------------------------------------------- holes of a printed body
def group_holes(cylinders, tol):
    """Concave cylinders on one axis line -> one group {"a", "base", "segs": [{"d", "t0", "t1", "span"}]}: t along "a"
    from "base". Faces that split one cylinder into halves are joined (their spans add)."""
    groups = []
    for c in cylinders:
        if not c.get('concave'):
            continue
        a, base = canon(c['o'], c['a'])
        g = next((g for g in groups if _ld(g['a'], g['base'], a, base) < tol['axis_mm']), None)
        if g is None:
            g = {'a': a, 'base': base, 'raw': []}
            groups.append(g)
        p0 = _add(c['o'], _mul(_norm(c['a']), c['t0'])); p1 = _add(c['o'], _mul(_norm(c['a']), c['t1']))
        t0, t1 = sorted((_dot(_sub(p0, g['base']), g['a']), _dot(_sub(p1, g['base']), g['a'])))
        g['raw'].append({'d': c['d'], 't0': t0, 't1': t1, 'span': c.get('span', 360.0), 'intrudes': c.get('intrudes', False)})
    for g in groups:
        g['segs'] = merge_segments(g.pop('raw'), tol)
    return groups


def merge_segments(raw, tol):
    """Same diameter and the same range: spans add (a cylinder split in two faces). Same diameter, touching ranges:
    one segment (a face split across its length). Sorted along t."""
    out = []
    for s in sorted(raw, key=lambda s: (round(s['d'] / tol['d']), s['t0'])):
        m = next((x for x in out if abs(x['d'] - s['d']) <= tol['d'] and abs(x['t0'] - s['t0']) < EPS_T and abs(x['t1'] - s['t1']) < EPS_T), None)
        if m:
            m['span'] = min(360.0, m['span'] + s['span'])
            continue
        out.append(dict(s))
    joined = []
    for s in sorted(out, key=lambda s: (s['t0'], s['d'])):
        m = next((x for x in joined if abs(x['d'] - s['d']) <= tol['d'] and s['t0'] <= x['t1'] + EPS_T and s['t1'] >= x['t0'] - EPS_T), None)
        if m:
            m['t0'], m['t1'] = min(m['t0'], s['t0']), max(m['t1'], s['t1']); m['span'] = min(m['span'], s['span'])
            m['intrudes'] = m.get('intrudes') or s.get('intrudes')
        else:
            joined.append(dict(s))
    return sorted(joined, key=lambda s: (s['t0'], -s['d']))


def perp(a):
    """Two unit vectors at right angles to a and to each other."""
    u = [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]
    e1 = _norm([a[1] * u[2] - a[2] * u[1], a[2] * u[0] - a[0] * u[2], a[0] * u[1] - a[1] * u[0]])
    e2 = [a[1] * e1[2] - a[2] * e1[1], a[2] * e1[0] - a[0] * e1[2], a[0] * e1[1] - a[1] * e1[0]]
    return e1, e2


def split_group(group, inside):
    """One axis line can hold several holes. inside(point) says whether a point is inside the body. The group is cut at
    a stretch with no cylinder when the axis there runs through plastic (two holes with a wall between them), or when
    the space beside the axis there is open (two holes facing each other across a cavity); a cone or a chamfer between
    two diameters keeps plastic beside the axis and joins them. Returns the list of groups."""
    segs = sorted(group['segs'], key=lambda s: s['t0'])
    out, cur, reach, wide = [], [], None, 0.0
    e1, e2 = perp(group['a'])
    for sg in segs:
        if cur and sg['t0'] > reach + EPS_T:
            mid = _add(group['base'], _mul(group['a'], (reach + sg['t0']) / 2))
            r = max(wide, sg['d']) / 2 + 1.0
            beside = [_add(mid, _mul(e, k * r)) for e in (e1, e2) for k in (1, -1)]
            if inside(mid) or any(not inside(q) for q in beside):
                out.append(cur); cur = []; reach = None; wide = 0.0
        cur.append(sg)
        reach = sg['t1'] if reach is None else max(reach, sg['t1'])
        wide = max(wide, sg['d'])
    if cur:
        out.append(cur)
    return [dict(group, segs=c) for c in out]


def ends(group):
    """(t_min, t_max) of the hole stack: Fusion tests a point just beyond each for inside/outside."""
    return min(s['t0'] for s in group['segs']), max(s['t1'] for s in group['segs'])


def walk(group, from_lo):
    """The profile from one end inwards: [{"d", "len", "span"}], "d": None for a stretch with no cylinder (a cone, a
    chamfer, a gap). Positions are distances from that end; where two segments overlap, the one met first counts."""
    lo, hi = ends(group)
    segs = [((s['t0'] - lo, s['t1'] - lo) if from_lo else (hi - s['t1'], hi - s['t0'])) + (s,) for s in group['segs']]
    prof, pos = [], 0.0
    for x0, x1, s in sorted(segs, key=lambda q: (q[0], -q[2]['d'])):
        if x1 <= pos + EPS_T:
            continue
        start = max(x0, pos)
        if start - pos > EPS_T:
            prof.append({'d': None, 'len': start - pos, 'span': 360.0})
        prof.append({'d': s['d'], 'len': x1 - start, 'span': s['span'], 'intrudes': s.get('intrudes', False)})
        pos = x1
    return prof


def fmt_profile(prof, limit=6):
    parts = []
    for p in prof[:limit]:
        parts.append(('cone/step %.2f' % p['len']) if p['d'] is None else ('D%.2f x %.2f' % (p['d'], p['len'])))
    return ' -> '.join(parts) + (' -> ...' if len(prof) > limit else '')


def fmt_expected(kind):
    pk = kind['pocket']
    return 'D%s x %.2f relief -> D%.2f x %.2f lead-in -> D%.2f to %.2f below the relief floor (or through)' % (
        '/'.join('%.1f' % d for d in pk['relief']['d']), pk['relief']['depth'], pk['lead_in']['d'], pk['lead_in']['depth'],
        pk['hole']['d'], pk['hole']['min_depth_below_relief'])


def _is(d, want, tol):
    return d is not None and abs(d - want) <= tol['d']


def pocket_at(prof, kinds, rules, blind=True):
    """(kind name, the profile from the relief on) when this end of a hole looks like an insert pocket, else None.
    Wider segments at the mouth (a recess, a bearing seat) are skipped: the pocket is in their floor."""
    tol = rules['tolerance']
    widest_relief = max(max(k['pocket']['relief']['d']) for k in kinds.values())
    i = 0
    while i < len(prof) and (prof[i]['d'] is None or prof[i]['d'] > widest_relief + tol['d']):
        i += 1
    rest = prof[i:]
    if not rest:
        return None
    within = rules['pocket_signature']['lead_in_within_mm']
    for name, k in kinds.items():
        pk = k['pocket']
        nxt = rest[1]['d'] if len(rest) > 1 else None
        if any(_is(rest[0]['d'], d, tol) for d in pk['relief']['d']) and (_is(nxt, pk['lead_in']['d'], tol) or _is(nxt, pk['hole']['d'], tol)):
            return name, rest, i
        pos = 0.0
        for j, p in enumerate(rest):
            if pos > within + tol['depth']:
                break
            if _is(p['d'], pk['lead_in']['d'], tol):
                # a lead-in is followed by the kind's hole, or ends blind (an old straight pocket); a D4.4 stretch that
                # runs on into another diameter and out of the part is a screw hole
                nxt = rest[j + 1]['d'] if j + 1 < len(rest) else None
                if _is(nxt, pk['hole']['d'], tol) or (j + 1 == len(rest) and blind):
                    return name, rest, i
            pos += p['len']
    return None


def forced_pocket(prof, rules):
    """An end of a hole that an insert sits in but that does not look like a pocket: read it as a pocket anyway (wider
    stretches at the mouth skipped as a recess) so that its deviations are named."""
    tol = rules['tolerance']
    widest = max(max(k['pocket']['relief']['d']) for k in rules['kinds'].values())
    i = 0
    while i < len(prof) and (prof[i]['d'] is None or prof[i]['d'] > widest + tol['d']):
        i += 1
    return prof[i:], i


def judge_pocket(kind, rest, through, tol):
    """Problems of one pocket against its kind: [(code, measured, expected, fix)]."""
    pk, out = kind['pocket'], []
    i = 0
    relief = rest[0] if rest and any(_is(rest[0]['d'], d, tol) for d in pk['relief']['d']) else None
    exp = fmt_expected(kind)
    if relief is None:
        if rest and rest[0]['d'] is not None and rest[0]['d'] > pk['lead_in']['d'] + tol['d']:
            out.append(('relief_d', 'D%.2f x %.2f at the mouth' % (rest[0]['d'], rest[0]['len']), 'relief D%s x %.2f' % (
                '/'.join('%.1f' % d for d in pk['relief']['d']), pk['relief']['depth']),
                'cut the mouth to a relief of the table: D%.1f x %.2f (D%s where D%.1f leaves too little wall)' % (
                    pk['relief']['d'][0], pk['relief']['depth'], '/'.join('%.1f' % d for d in pk['relief']['d'][1:]), pk['relief']['d'][0])))
            i = 1
        else:
            out.append(('relief_missing', 'no relief: the pocket opens straight onto the face', 'relief D%.1f x %.2f' % (pk['relief']['d'][0], pk['relief']['depth']),
                        'cut the melt relief D%.1f x %.2f at the mouth (D%s where D%.1f leaves too little wall): insert_pockets.relief()' % (
                            pk['relief']['d'][0], pk['relief']['depth'], '/'.join('%.1f' % d for d in pk['relief']['d'][1:]), pk['relief']['d'][0])))
    else:
        i = 1
        if abs(relief['len'] - pk['relief']['depth']) > tol['depth']:
            out.append(('relief_depth', 'relief D%.2f x %.2f' % (relief['d'], relief['len']), 'depth %.2f' % pk['relief']['depth'],
                        'make the relief %.2f deep (whole layers of 0.18)' % pk['relief']['depth']))
    floor_depth = sum(p['len'] for p in rest[:i])            # from the pocket's mouth to the relief floor
    lead = rest[i] if i < len(rest) else None
    if lead is None or not _is(lead['d'], pk['lead_in']['d'], tol):
        got = 'D%.2f' % lead['d'] if lead and lead['d'] else ('a cone' if lead else 'nothing')
        if lead and lead['d'] and _is(lead['d'], pk['hole']['d'], tol):
            out.append(('lead_in_missing', 'the hole D%.2f starts at the relief floor' % lead['d'], 'lead-in D%.2f x %.2f' % (pk['lead_in']['d'], pk['lead_in']['depth']),
                        'cut the D%.2f x %.2f lead-in below the relief floor' % (pk['lead_in']['d'], pk['lead_in']['depth'])))
            j = i
        else:
            out.append(('lead_in_d', '%s below the relief floor' % got, 'lead-in D%.2f x %.2f' % (pk['lead_in']['d'], pk['lead_in']['depth']),
                        'recut the pocket from the table (insert_pockets.convert() for an old straight pocket, cut() for a new one)'))
            j = i + 1
    else:
        j = i + 1
        if abs(lead['len'] - pk['lead_in']['depth']) > tol['depth']:
            out.append(('lead_in_depth', 'lead-in D%.2f x %.2f' % (lead['d'], lead['len']), 'depth %.2f' % pk['lead_in']['depth'],
                        'make the lead-in %.2f deep' % pk['lead_in']['depth']))
    need = pk['hole']['min_depth_below_relief']
    hole = rest[j] if j < len(rest) else None
    if hole is None:
        if not through:
            out.append(('hole_missing', 'nothing below the lead-in', 'D%.2f to %.2f below the relief floor' % (pk['hole']['d'], need),
                        'cut the D%.2f hole to %.2f below the relief floor' % (pk['hole']['d'], need)))
    elif not _is(hole['d'], pk['hole']['d'], tol):
        out.append(('hole_d', 'D%.2f below the lead-in' % hole['d'] if hole['d'] else 'a cone below the lead-in', 'D%.2f' % pk['hole']['d'],
                    'recut the hole to D%.2f (a wider hole lets the insert spin, a narrower one bulges into its thread)' % pk['hole']['d']))
    else:
        reach = sum(p['len'] for p in rest[i:j + 1])           # from the relief floor to the end of the hole segment
        nxt = rest[j + 1] if j + 1 < len(rest) else None
        # short is fine only where the hole runs out of the part (through): the plastic and the screw tip leave that way
        if reach < need - tol['depth'] and not (nxt is None and through):
            what = 'a blind floor' if nxt is None else ('D%.2f' % nxt['d'] if nxt['d'] else 'a cone')
            out.append(('hole_short', 'the D%.2f hole ends %.2f below the relief floor (then %s)' % (hole['d'], reach, what),
                        '%.2f below the relief floor' % need,
                        'deepen the D%.2f hole to %.2f below the relief floor: the displaced plastic and the screw tip need the room' % (hole['d'], need)))
    depth_needed = floor_depth + need
    pos = 0.0
    for p in rest:
        if pos >= depth_needed - tol['depth']:
            break
        if p['d'] is not None and p.get('intrudes'):
            out.append(('not_round', 'plastic inside the D%.2f circle on one side (a sliver left by two cuts that do not meet)' % p['d'], 'a round D%.2f' % p['d'],
                        'recut that step of the pocket in one cut from the table'))
            break
        if p['d'] is not None and p['span'] < tol['full_circle_deg']:
            out.append(('open_wall', 'D%.2f covers %.0f degrees' % (p['d'], p['span']), 'a full circle', 'close the pocket wall: the insert has nothing to grip on that side'))
            break
        pos += p['len']
    return out, floor_depth


# ---------------------------------------------------------------- inserts
def insert_shape(ins, tol):
    """{"a", "base", "t0", "t1", "length", "outer": [(d, t0, t1)], "bore": [d], "volume"} of an insert body."""
    cyl = ins['cylinders']
    outer = [c for c in cyl if not c.get('concave')]
    ref = max(outer or cyl, key=lambda c: c['d']) if (outer or cyl) else None
    if ref is None:
        return None
    a, base = canon(ref['o'], ref['a'])
    def t_of(c):
        p0 = _add(c['o'], _mul(_norm(c['a']), c['t0'])); p1 = _add(c['o'], _mul(_norm(c['a']), c['t1']))
        return sorted((_dot(_sub(p0, base), a), _dot(_sub(p1, base), a)))
    segs = [(c['d'], *t_of(c), c.get('concave', False)) for c in cyl if _ld(a, base, *canon(c['o'], c['a'])) < tol['axis_mm']]
    t0 = min(s[1] for s in segs); t1 = max(s[2] for s in segs)
    return {'a': a, 'base': base, 't0': t0, 't1': t1, 'length': t1 - t0,
            'outer': sorted([(round(s[0], 3), s[1], s[2]) for s in segs if not s[3]], key=lambda x: x[1]),
            'bore': sorted({round(s[0], 3) for s in segs if s[3]}), 'volume': ins.get('volume')}


def golden_check(shape, kind, tol):
    """None when the insert is the golden model of `kind`, else what differs. Also (pilot end t) for orientation."""
    g = kind['golden']
    if shape is None:
        return 'no cylinder at all', None
    outer = {}
    for d, t0, t1 in shape['outer']:
        outer.setdefault(round(d, 2), []).append((t0, t1))
    want = {round(g['knurl_d'], 2): g['length'] - g['pilot_len'], round(g['pilot_d'], 2): g['pilot_len']}
    diffs = []
    if abs(shape['length'] - g['length']) > tol['depth']:
        diffs.append('length %.2f (golden %.2f)' % (shape['length'], g['length']))
    for d, L in want.items():
        got = sum(t1 - t0 for t0, t1 in outer.get(d, []))
        if abs(got - L) > tol['depth']:
            diffs.append('D%.1f over %.2f (golden %.2f)' % (d, got, L))
    extra = sorted(d for d in outer if d not in want)
    if extra:
        diffs.append('outer D%s (golden has only D%.1f and D%.1f)' % ('/'.join('%.2f' % d for d in extra), g['knurl_d'], g['pilot_d']))
    if [round(b, 2) for b in shape['bore']] != [round(g['bore_d'], 2)]:
        diffs.append('bore %s (golden D%.1f)' % ('/'.join('D%.2f' % b for b in shape['bore']) or 'none', g['bore_d']))
    pilot = outer.get(round(g['pilot_d'], 2))
    pilot_t = (sum(t0 + t1 for t0, t1 in pilot) / (2 * len(pilot))) if pilot else None
    return ('; '.join(diffs) or None), pilot_t


def describe_shape(shape):
    if shape is None:
        return 'no cylinders'
    return 'length %.2f, outer %s%s' % (shape['length'], ' + '.join('D%.2f' % d for d, _, _ in shape['outer']) or 'none',
                                         (', bore D%s' % '/'.join('%.2f' % b for b in shape['bore'])) if shape['bore'] else ', solid')



def judge_inserts(pockets, kinds, tol):
    findings = []
    for p in pockets:
        kind = kinds[p['kind']]
        base = {'part': p['part'], 'kind': p['kind'], 'at': _at(p), 'out': _out(p), 'profile': fmt_profile(p['profile'])}
        for s in p['inserts'][1:]:
            findings.append(dict(base, code='two_inserts', measured='%d insert models on one pocket' % len(p['inserts']), expected='one',
                                 fix='delete the extra insert body %s' % s['ins'].get('path')))
        for s in p['inserts'][:1]:
            sh, ins = s['shape'], s['ins']
            diff, pilot_t = golden_check(sh, kind, tol)
            ref = dict(base, insert=ins.get('path'))
            if diff:
                findings.append(dict(ref, code='not_golden', measured='%s (%s)' % (describe_shape(sh), diff),
                                     expected='"%s": %s' % (kind['golden']['name'], 'length %.1f, D%.1f knurl + D%.1f x %.1f pilot, bore D%.1f' % (
                                         kind['golden']['length'], kind['golden']['knurl_d'], kind['golden']['pilot_d'], kind['golden']['pilot_len'], kind['golden']['bore_d'])),
                                     fix='replace it with the golden model at the same place: gate.py fix-inserts PART'))
            if s['dist'] > tol['axis_mm']:
                findings.append(dict(ref, code='off_axis', measured='insert axis %.2f mm off the pocket axis' % s['dist'], expected='within %.2f' % tol['axis_mm'],
                                     fix='move the insert (or the pocket) onto one axis'))
            # the insert's end nearer the mouth must be on the relief floor; the pilot end deeper
            near = min((s['ta'], s['tb']), key=lambda t: (t - p['t_mouth']) * p['sign'])
            far = max((s['ta'], s['tb']), key=lambda t: (t - p['t_mouth']) * p['sign'])
            off = (near - p['t_floor']) * p['sign']
            if abs(off) > tol['flush_mm']:
                findings.append(dict(ref, code='not_flush', measured='insert top %.2f mm %s the relief floor' % (abs(off), 'below' if off > 0 else 'above'),
                                     expected='flush with the relief floor (within %.2f)' % tol['flush_mm'],
                                     fix='move the insert along its axis by %.2f mm %s (gate.py fix-inserts PART does it)' % (abs(off), 'out of the pocket' if off > 0 else 'into the pocket')))
            if not diff and pilot_t is not None:
                pt = _dot(_sub(_add(sh['base'], _mul(sh['a'], pilot_t)), p['base']), p['a'])
                if (pt - (near + far) / 2) * p['sign'] < 0:
                    findings.append(dict(ref, code='upside_down', measured='the pilot band points out of the pocket', expected='the pilot band at the bottom',
                                         fix='turn the insert end for end (gate.py fix-inserts PART)'))
    return findings


def _at(p):
    return [round(x, 3) for x in _add(p['base'], _mul(p['a'], p['t_mouth']))]


def _out(p):
    return [round(-x * p['sign'], 6) for x in p['a']]

# ---------------------------------------------------------------- the whole check
def check(parts, inserts, rules, outside_hosts=None):
    """parts: the printed bodies with "groups" (each with "open_lo", "open_hi" from Fusion). inserts: every insert body
    of the document. Returns {"pockets": [...], "findings": [...], "inserts": {...counts}}."""
    tol, kinds = rules['tolerance'], rules['kinds']
    pockets = []
    for part in parts:
        for gi, g in enumerate(part['groups']):
            lo, hi = ends(g)
            for from_lo, is_open in ((True, g.get('open_lo')), (False, g.get('open_hi'))):
                if not is_open:
                    continue
                prof = walk(g, from_lo)
                hit = pocket_at(prof, kinds, rules, blind=not (g.get('open_hi') if from_lo else g.get('open_lo')))
                if not hit:
                    continue
                name, rest, skipped = hit
                t_mouth = (lo + sum(p['len'] for p in prof[:skipped])) if from_lo else (hi - sum(p['len'] for p in prof[:skipped]))
                sign = 1 if from_lo else -1                     # inwards along g["a"]
                through = bool(g.get('open_hi') if from_lo else g.get('open_lo'))
                problems, floor_depth = judge_pocket(kinds[name], rest, through, tol)
                if skipped:                                     # the pocket is in the floor of a wider recess
                    rd = max(p['d'] for p in prof[:skipped] if p['d']) if any(p['d'] for p in prof[:skipped]) else None
                    problems = [(c, (m + ' (at the bottom of a %s recess %.2f deep: the recess, a washer seat, is not the relief)' % (
                        'D%.1f' % rd if rd else 'wide', sum(p['len'] for p in prof[:skipped]))) if c == 'relief_missing' else m, e,
                                 (f + ', in the floor of the recess') if c == 'relief_missing' else f) for c, m, e, f in problems]
                pockets.append({'part': part['name'], 'kind': name, 'group': gi, 'a': g['a'], 'base': g['base'], 'sign': sign,
                                't_mouth': t_mouth, 't_floor': t_mouth + sign * floor_depth, 'profile': rest, 'problems': problems,
                                'through': through, 'inserts': []})
    shapes = []
    for ins in inserts:
        sh = insert_shape(ins, tol)
        shapes.append({'ins': ins, 'shape': sh, 'pocket': None})
    # match each insert to the pocket whose axis it sits on (0.5 mm and inside the pocket's first 8 mm)
    for s in shapes:
        sh = s['shape']
        if sh is None:
            continue
        best = None
        host = s['ins'].get('host')
        for p in pockets:
            if host and p['part'] != host:           # the insert sits in another part's plastic: not this pocket
                continue
            dist = line_distance(p['a'], p['base'], sh['a'], sh['base'])
            if dist is None or dist > 0.5:
                continue
            ta = _dot(_sub(_add(sh['base'], _mul(sh['a'], sh['t0'])), p['base']), p['a'])
            tb = _dot(_sub(_add(sh['base'], _mul(sh['a'], sh['t1'])), p['base']), p['a'])
            lo, hi = sorted((ta, tb))
            span = sorted((p['t_mouth'], p['t_mouth'] + p['sign'] * 8.0))
            if hi < span[0] - 0.5 or lo > span[1] + 0.5:
                continue
            if best is None or dist < best[0]:
                best = (dist, p, ta, tb)
        if best:
            s['pocket'] = best[1]; s['dist'] = best[0]; s['ta'], s['tb'] = best[2], best[3]
            best[1]['inserts'].append(s)
    findings = []
    def at(p):
        return [round(x, 3) for x in _add(p['base'], _mul(p['a'], p['t_mouth']))]
    def out_dir(p):
        return [round(-x * p['sign'], 6) for x in p['a']]
    for p in pockets:
        kind = kinds[p['kind']]
        base = {'part': p['part'], 'kind': p['kind'], 'at': at(p), 'out': out_dir(p), 'profile': fmt_profile(p['profile'])}
        for code, measured, expected, fix in p['problems']:
            findings.append(dict(base, code=code, measured=measured, expected=expected, fix=fix))
        if not p['inserts']:
            findings.append(dict(base, code='no_insert', measured='a %s pocket shape with no insert model on its axis' % p['kind'],
                                 expected='the golden insert "%s" flush with the relief floor' % kind['golden']['name'],
                                 fix='place the golden insert: gate.py fix-inserts PART (or, if this hole holds no insert, make it not look like a pocket)'))
    findings.extend(judge_inserts(pockets, kinds, tol))
    unmatched = [s for s in shapes if s['pocket'] is None]
    hosts = set(p['name'] for p in parts)
    counts = {'inserts': len(shapes), 'in_pockets': len(shapes) - len(unmatched), 'pockets': len(pockets)}
    loose = []
    for s in unmatched:
        sh = s['shape']
        hit = None
        for part in parts:
            if sh is None:
                break
            if part['name'] != s['ins'].get('host'):   # only the hole of the part whose plastic holds the insert
                continue
            for g in part['groups']:
                dist = line_distance(g['a'], g['base'], sh['a'], sh['base'])
                if dist is None or dist > 0.5:
                    continue
                ta = _dot(_sub(_add(sh['base'], _mul(sh['a'], sh['t0'])), g['base']), g['a'])
                tb = _dot(_sub(_add(sh['base'], _mul(sh['a'], sh['t1'])), g['base']), g['a'])
                lo, hi = ends(g)
                if max(ta, tb) < lo - 0.5 or min(ta, tb) > hi + 0.5:
                    continue
                # the end of the hole the insert is nearer to is its mouth
                from_lo = abs(min(ta, tb) - lo) <= abs(max(ta, tb) - hi)
                if hit is None or dist < hit[0]:
                    hit = (dist, part, g, from_lo, ta, tb)
        if hit:
            dist, part, g, from_lo, ta, tb = hit
            kname = next(iter(kinds))
            prof = walk(g, from_lo)
            rest, skipped = forced_pocket(prof, rules)
            lo, hi = ends(g)
            t_mouth = (lo + sum(p['len'] for p in prof[:skipped])) if from_lo else (hi - sum(p['len'] for p in prof[:skipped]))
            sign = 1 if from_lo else -1
            through = bool(g.get('open_hi') if from_lo else g.get('open_lo'))
            problems, floor_depth = judge_pocket(kinds[kname], rest, through, tol) if rest else ([('hole_missing', 'no hole under the insert', fmt_expected(kinds[kname]), 'cut the pocket from the table (insert_pockets.cut)')], 0.0)
            p = {'part': part['name'], 'kind': kname, 'a': g['a'], 'base': g['base'], 'sign': sign, 't_mouth': t_mouth,
                 't_floor': t_mouth + sign * floor_depth, 'profile': rest, 'problems': [('not_a_pocket', 'the insert sits in a hole that is not a %s pocket' % kname,
                 fmt_expected(kinds[kname]), 'recut the hole as a pocket of the table (insert_pockets.convert() or cut())')] + problems,
                 'through': through, 'inserts': [s]}
            s['pocket'] = p; s['dist'] = dist; s['ta'], s['tb'] = ta, tb
            pockets.append(p)
            judged = judge_inserts([p], kinds, tol)
            base = {'part': p['part'], 'kind': kname, 'at': at(p), 'out': out_dir(p), 'profile': fmt_profile(p['profile'])}
            for code, measured, expected, fix in p['problems']:
                findings.append(dict(base, code=code, measured=measured, expected=expected, fix=fix))
            findings.extend(judged)
            counts['in_pockets'] += 1
            continue
        host = s['ins'].get('host')
        if host in hosts:
            c = _add(sh['base'], _mul(sh['a'], (sh['t0'] + sh['t1']) / 2)) if sh else None
            findings.append({'part': host, 'code': 'insert_without_pocket', 'insert': s['ins'].get('path'), 'at': [round(x, 3) for x in c] if c else None,
                             'out': [round(x, 6) for x in sh['a']] if sh else None,
                             'measured': 'an insert (%s) with no hole on its axis' % describe_shape(sh),
                             'expected': 'a pocket of the table under it', 'fix': 'cut the pocket from the table at the insert (insert_pockets.cut), or move the insert into its pocket'})
        else:
            loose.append(s['ins'].get('path'))
    counts['pockets'] = len(pockets)
    counts['outside_printed_parts'] = len(loose)
    return {'pockets': pockets, 'findings': findings, 'counts': counts, 'outside': loose}
