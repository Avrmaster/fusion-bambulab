"""Feature anchors, context hashes and waivers.

An anchor says WHICH feature of a part a finding, a waiver or an attest answer is about:
    {"kind": "insert_pocket" | "hole" | "bearing" | "screw" | "face" | "part", "axis": [x, y, z], "point": [x, y, z]}
in the PART's own frame, mm. `axis` is a unit vector (for a hole it points out of the material at the mouth, for a face
it is the outward normal), stored to 4 decimals; `point` is where the axis leaves the material, rounded to 0.05 mm.
The anchor of a whole part is {"kind": "part", "axis": [0, 0, 0], "point": [0, 0, 0]}.

Two anchors are the same feature when the kind is equal, the axes point the same way (2 degrees), the points are at most
0.3 mm apart ACROSS the axis and at most 2.0 mm apart ALONG it. The second number is why a waiver survives a recut mouth:
a melt relief or a landing cut moves the point along the axis only (keys made from the pocket top were lost by a 0.54 mm
cut). Two pockets on one line from opposite faces differ by the axis direction.

The context hash is a hash of the facts a verdict was computed from (profile, blocking neighbour, measured value),
numbers rounded to 0.05. Same anchor + same context = nothing about this feature changed.

A waiver {"rule", "part", "anchor", "context", "reason", "quote", "date"} is
    valid    a finding of that rule and part has the anchor and the same context
    expired  the anchor is still there (a finding with another context, or a feature with no finding any more)
    orphan   no finding and no feature has that anchor
A reason that is only an order to export ("yes, export") is rejected: it must say why the feature may stay."""
import math, re
from gate_common import digest

KINDS = ('insert_pocket', 'hole', 'bearing', 'screw', 'face', 'part')
GRID = 0.05                 # mm: rounding of anchor points and of numbers inside a context
TOL = 0.3                   # mm: two anchor points this close across the axis are one feature
TOL_ALONG = 2.0             # mm: allowed shift along the axis (a recut mouth)
TOL_AXIS_DEG = 2.0
WAIVER_KEYS = ('rule', 'part', 'anchor', 'context', 'reason', 'quote', 'date')
_FILLER = set('yes yeah yep ok okay please just go ahead it now anyway the this that these those part parts a an and then to we '
              'you i he she owner user said says told so do can may will lets let s me us my is as of for all'.split())
_ORDER = set('export exported exporting print printed printing proceed ship shipped continue approve approved accept accepted waive '
             'waived skip skipped ignore ignored pass passed fine good done confirmed confirm agreed agree allowed allow '
             'stamp stamped release released send sent'.split())
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def round_to(x, step=GRID):
    """x rounded to a multiple of `step`, as a clean float (0.30000000000000004 -> 0.3, -0.0 -> 0.0)."""
    v = round(round(float(x) / step) * step, 6)
    return 0.0 if v == 0 else v


def _unit(axis):
    n = math.sqrt(sum(float(c) * float(c) for c in axis))
    if n < 1e-9:
        return [0.0, 0.0, 0.0]
    out = [round(float(c) / n, 4) for c in axis]
    return [0.0 if c == 0 else c for c in out]


def anchor(kind, axis=(0, 0, 0), point=(0, 0, 0)):
    """A normalised anchor: the axis as a unit vector to 4 decimals, the point rounded to 0.05 mm.
    kind is one of KINDS; a "part" anchor always has zero axis and point. ValueError for a bad kind or shape."""
    if kind not in KINDS:
        raise ValueError('anchor kind must be one of %s, got %r' % (', '.join(KINDS), kind))
    if kind == 'part':
        return {'kind': 'part', 'axis': [0.0, 0.0, 0.0], 'point': [0.0, 0.0, 0.0]}
    if len(axis) != 3 or len(point) != 3:
        raise ValueError('anchor axis and point are [x, y, z]')
    return {'kind': kind, 'axis': _unit(axis), 'point': [round_to(c) for c in point]}


def part_anchor():
    """The anchor of a whole part."""
    return anchor('part')


def anchor_key(a):
    """One text for an anchor, for dictionary keys and ids: 'hole|0.0,0.0,1.0|-18.0,-31.2,39.7'."""
    a = anchor(a['kind'], a.get('axis', (0, 0, 0)), a.get('point', (0, 0, 0)))
    return '%s|%s|%s' % (a['kind'], ','.join(repr(c) for c in a['axis']), ','.join(repr(c) for c in a['point']))


def anchor_distance(a, b):
    """(across, along) in mm between the points of two anchors, measured against the axis of `a`.
    With a zero axis: (plain distance, 0.0)."""
    d = [float(b['point'][i]) - float(a['point'][i]) for i in range(3)]
    ax = [float(c) for c in a.get('axis', (0, 0, 0))]
    n = math.sqrt(sum(c * c for c in ax))
    if n < 1e-9:
        return math.sqrt(sum(c * c for c in d)), 0.0
    along = sum(d[i] * ax[i] for i in range(3)) / n
    across = math.sqrt(max(0.0, sum(c * c for c in d) - along * along))
    return across, abs(along)


def anchors_match(a, b, tol=TOL, tol_along=TOL_ALONG, tol_axis_deg=TOL_AXIS_DEG):
    """Are two anchors the same feature? Same kind, axes within `tol_axis_deg` (same direction, not opposite), points
    within `tol` across the axis and `tol_along` along it. Two "part" anchors always match."""
    if not a or not b or a.get('kind') != b.get('kind'):
        return False
    if a['kind'] == 'part':
        return True
    ax, bx = [float(c) for c in a['axis']], [float(c) for c in b['axis']]
    na, nb = math.sqrt(sum(c * c for c in ax)), math.sqrt(sum(c * c for c in bx))
    if (na < 1e-9) != (nb < 1e-9):
        return False
    if na >= 1e-9:
        cos = sum(ax[i] * bx[i] for i in range(3)) / (na * nb)
        if cos < math.cos(math.radians(tol_axis_deg)) - 1e-9:
            return False
    across, along = anchor_distance(a, b)
    if na < 1e-9:
        return across <= tol + 1e-9
    return across <= tol + 1e-9 and along <= tol_along + 1e-9


def find(a, items, tol=TOL, tol_along=TOL_ALONG):
    """The item of `items` (dicts with an "anchor" key: findings, census features) whose anchor matches `a` and lies
    nearest to it, or None."""
    best, best_d = None, None
    for it in items or []:
        other = it.get('anchor')
        if other and anchors_match(a, other, tol, tol_along):
            across, along = anchor_distance(a, other)
            d = across + along
            if best is None or d < best_d:
                best, best_d = it, d
    return best


def feature_id(prefix, rule, part, anchor_, context):
    """A short stable id for "this rule on this feature in this state": prefix + '-' + 6 hex over rule, part, the
    anchor and the context hash. finding_id() and gate_attest.question_id() are made with it."""
    return '%s-%s' % (prefix, digest([rule, part, anchor_key(anchor_ or part_anchor()), context], 6))


def finding_id(finding):
    """'F-3fa2c1' for a finding: the id `gate.py owner waive` and the reports use. Same feature, same context = same id."""
    return feature_id('F', finding.get('rule'), finding.get('part'), finding.get('anchor'), finding.get('context'))


def _rounded(x, step):
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return round_to(x, step)
    if isinstance(x, dict):
        return {str(k): _rounded(v, step) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_rounded(v, step) for v in x]
    raise TypeError('a context holds numbers, text, lists and objects, not %s' % type(x).__name__)


def context_hash(data, step=GRID):
    """16 hex characters over `data` (the facts a verdict was computed from) with every number rounded to `step`.
    Key order does not matter; list order does (sort a list first when its order is not a fact)."""
    return digest(_rounded(data, step), 16)


def reason_error(reason):
    """None when the text can stand as the reason of a waiver, else why not. Rejected: nothing, fewer than three words
    that say something, and an order to export ("yes, export", "ok print it", "approved, go ahead")."""
    if not isinstance(reason, str) or not reason.strip():
        return 'the reason is empty: say why the feature may stay as it is'
    words = re.findall(r"[^\W_]+", reason.lower())
    content = [w for w in words if w not in _FILLER and w not in _ORDER]
    if len(content) < 3:
        if any(w in _ORDER for w in words):
            return 'the reason is an order to export, not a reason: say why the feature may stay as it is'
        return 'the reason is too short: say what stops the fix and why the feature may stay'
    return None


def validate_waiver(w):
    """Problems of one waiver as a list of texts (empty = sound): every key of WAIVER_KEYS present, a real anchor,
    a context hash, a reason that is not an export order, the owner's words in "quote", a date YYYY-MM-DD."""
    errors = []
    if not isinstance(w, dict):
        return ['a waiver must be an object']
    for k in WAIVER_KEYS:
        if k not in w:
            errors.append('waiver: missing "%s"' % k)
    if errors:
        return errors
    a = w['anchor']
    if not isinstance(a, dict) or a.get('kind') not in KINDS or len(a.get('axis', ())) != 3 or len(a.get('point', ())) != 3:
        errors.append('waiver: "anchor" must be {"kind", "axis": [x, y, z], "point": [x, y, z]}')
    if not isinstance(w['context'], str) or not w['context']:
        errors.append('waiver: "context" must be the context hash of the finding it answers')
    r = reason_error(w['reason'])
    if r:
        errors.append('waiver: ' + r)
    if not isinstance(w['quote'], str) or not w['quote'].strip():
        errors.append('waiver: "quote" must hold the owner\'s own words')
    if not isinstance(w['date'], str) or not _DATE.match(w['date']):
        errors.append('waiver: "date" must be YYYY-MM-DD')
    for k in ('rule', 'part'):
        if not isinstance(w[k], str) or not w[k]:
            errors.append('waiver: "%s" must be text' % k)
    return errors


def waiver_state(waiver, findings, features=None):
    """('valid' | 'expired' | 'orphan', the finding it answers or None).

    findings  the findings of the part at its current signature (any rule: only those of the waiver's rule and part count)
    features  optional: every feature of the part (dicts with "anchor"), so that a waiver whose finding is gone because the
              feature was fixed reads 'expired' and not 'orphan'"""
    mine = [f for f in findings or [] if f.get('rule') == waiver.get('rule') and f.get('part') == waiver.get('part')
            and anchors_match(waiver.get('anchor'), f.get('anchor'))]
    same = [f for f in mine if f.get('context') == waiver.get('context')]
    if same:
        return 'valid', find(waiver['anchor'], same)
    if mine:
        return 'expired', find(waiver['anchor'], mine)
    if find(waiver.get('anchor') or {}, features) is not None:
        return 'expired', None
    return 'orphan', None


def apply_waivers(findings, waivers, features=None, part=None):
    """Split the findings of ONE part by the owner's waivers.

    findings  the findings of the part at its current signature
    waivers   the "waivers" list of gate/owner/waivers.json (waivers of other parts are skipped)
    features  optional, as in waiver_state()
    part      the part id; needed when `findings` is empty, otherwise taken from the findings

    Returns (open, waived, rows): `open` the findings no valid waiver answers, `waived` the others (each a copy with
    "waiver": the waiver's id or index), `rows` one {"waiver": id or index, "rule", "part", "state", "finding": the
    finding or None} per waiver of this part. A waiver that fails validate_waiver() never waives anything: its row has
    "state": "invalid" and "errors"."""
    findings = list(findings or [])
    parts = {part} if part else {f.get('part') for f in findings}
    taken, rows = {}, []
    for i, w in enumerate(waivers or []):
        wid = w.get('id', i) if isinstance(w, dict) else i
        if isinstance(w, dict) and parts and w.get('part') not in parts:
            continue
        errors = validate_waiver(w)
        if errors:
            rows.append({'waiver': wid, 'rule': w.get('rule') if isinstance(w, dict) else None,
                         'part': w.get('part') if isinstance(w, dict) else None, 'state': 'invalid', 'finding': None, 'errors': errors})
            continue
        state, f = waiver_state(w, findings, features)
        rows.append({'waiver': wid, 'rule': w['rule'], 'part': w['part'], 'state': state, 'finding': f})
        if state == 'valid':
            taken[id(f)] = wid
    open_, waived = [], []
    for f in findings:
        if id(f) in taken:
            waived.append(dict(f, waiver=taken[id(f)]))
        else:
            open_.append(f)
    return open_, waived, rows
