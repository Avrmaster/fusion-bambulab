"""Judges: PURE functions, facts + parameters -> findings. No Fusion, no files, no clock.

Every judge has the same shape:

    @judge
    def inserts_wall(facts, params, ctx):
        return [finding(...), ...]

    facts   the "facts" object of the probe the rule names in its check (INTERFACES.md, "Facts"), never None
    params  flat values, gate_params.values(): {"inserts.wall_target": 4.0, "screws.min_engagement[M3]": 3.2, ...};
            read them with pv(params, name, key=None)
    ctx     {"part": "<doc key>/<slug>", "rule": rule id, "tables": {"inserts": ..., "screws": ..., "bearings": ...,
             "washers": ..., "pockets": ...}, "target": the declared target ({"nozzle", "line_width", "material",
             "layer_height", "xy_hole_compensation", ...}) or None,
             "census": the fusion_census facts of the part at this signature or None,
             "prev": the same probe's facts at the last checked signature of the part or None,
             "intent": part ids named by `gate.py run --parts`, or None,
             "print": how the part lies on the bed, {"up": unit vector of the print axis in the part frame (away from
             the bed) or None, "bed": the height of the bed face along "up" or None, "source": where it comes from,
             "why": text when it is not known} or None (gate_plan.print_frame)}
    The tag of a fit parameter comes with the values as "<name>#tag" ("M", "U", "D", "S"): it is the tag for the
    declared target the part is judged for (gate_params.values).
    returns a list of findings made with finding(); an empty list is a pass

The judge of a rule is named after the rule: inserts.wall -> inserts_wall. One judge serves one rule.
A judge never raises for bad geometry: what it cannot decide is a NOT_RUN finding that says why.
Every judge has at least one fixture tests/fixtures/facts/<judge>__<case>.json (tests/test_judge_fixtures.py runs them).

Verdicts: FAIL, GROW, BLOCKED and NOT_RUN stop the stamp; INFO never does.
A finding with "ask": <attest rule id> is closed by an answer to that question for the same anchor and context
(gate_attest) as well as by a waiver; until then it counts as an open question (exit 2), not as a block row (exit 1).

How every judge here reads its facts:
- A list the probe should have written and did not (no "pockets", no "regions") is NOT_RUN, never a pass. An empty list
  is a pass: the probe looked and found none.
- A row without the number the rule needs is NOT_RUN for that row.
- The limit is the parameter. A measure equal to the limit passes; nothing is subtracted from a limit "for tolerance"
  (only 1e-6 for float noise). A probe that measures in steps must refine its number itself.
- When the facts say how far the probe looked ("ray"."max", a path's "reach", "margin", "report_under", "width_cap") and
  that is short of today's limit, the answer is NOT_RUN: "nothing found" is not "not looked that far".
- Waivers and attest answers are not seen here: the caller lays them over the findings."""
import hashlib, inspect, math, re, types
from gate_common import VERDICTS
import gate_features

JUDGES = {}                 # name -> function; filled by @judge
_HASHES = {}                # (name, function) -> judge_hash: the source of a function does not change while the process runs
_EPS = 1e-6                 # float noise only: 3.9999999 is 4.0, 3.99 is not


def judge(fn):
    """Register a judge under its function name."""
    JUDGES[fn.__name__] = fn
    return fn


def pv(params, name, key=None):
    """The value of a parameter out of the flat dict: "name[key]" when a per-size value exists, else "name".
    KeyError when the judge asks for a parameter that was not handed to it (the rule must list it in "params")."""
    if key is not None and '%s[%s]' % (name, key) in params:
        return params['%s[%s]' % (name, key)]
    return params[name]


def finding(rule, part, verdict, text, anchor=None, measure=None, limit=None, unit='mm', context=None, by=None, fix=None, ask=None):
    """One finding:
    {"rule", "part", "verdict", "measure", "limit", "unit", "anchor", "context", "text", "by", "fix", "ask"}

    rule, part  ids (take them from ctx)
    verdict     one of FAIL GROW BLOCKED NOT_RUN INFO
    text        one line a person can act on: where, how much, against what
    anchor      gate_features.anchor(...) of the feature; None = the whole part
    measure, limit, unit   the measured number and the limit it was held against (None when the finding has no number)
    context     the facts the verdict rests on (any JSON value, hashed here), or a ready 16-hex hash;
                None = hashed from verdict, measure and by
    by          what stops the fix or what the feature is blocked by: a list of names (a single text becomes a list)
    fix         ordered list of what to do; None = run_judge() fills in the rule's fix_order
    ask         id of the attest rule whose answer closes this finding, or None"""
    if verdict not in VERDICTS:
        raise ValueError('verdict must be one of %s, got %r' % (', '.join(VERDICTS), verdict))
    by = [] if by is None else ([by] if isinstance(by, str) else list(by))
    if context is None:
        context = {'verdict': verdict, 'measure': measure, 'by': sorted(by)}
    if not (isinstance(context, str) and len(context) == 16 and all(c in '0123456789abcdef' for c in context)):
        context = gate_features.context_hash(_plain(context))
    return {'rule': rule, 'part': part, 'verdict': verdict, 'measure': measure, 'limit': limit, 'unit': unit,
            'anchor': anchor or gate_features.part_anchor(), 'context': context, 'text': text, 'by': by,
            'fix': list(fix) if fix else [], 'ask': ask}


def _plain(x):
    """A context value the hash can take whatever a probe wrote: NaN, infinity, numbers too large to round and anything
    that is not JSON become text."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, (int, float)):
        return x if x == x and abs(x) < 1e9 else repr(x)
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return repr(x)


def _code_names(code):
    """Every global and attribute name a code object reads, with the lambdas and comprehensions inside it."""
    names = set(code.co_names)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            names |= _code_names(c)
    return names


def _judge_sources(fn):
    """The source text of a judge and of every private helper (leading underscore) of its module that it reaches,
    directly or through another helper, sorted by name."""
    seen, todo = {}, [fn]
    while todo:
        f = todo.pop()
        if f.__name__ in seen:
            continue
        seen[f.__name__] = inspect.getsource(f)
        for n in _code_names(f.__code__):
            g = f.__globals__.get(n)
            if n.startswith('_') and isinstance(g, types.FunctionType) and g.__module__ == fn.__module__:
                todo.append(g)
    return [seen[k] for k in sorted(seen)]


def judge_hash(name):
    """16 hex characters over the source text of one judge function and of the private helpers of this file it calls
    (a judge that changes only through its helper is a changed judge). A stamp stores it per check; when it differs
    from today's value the judge changed and only that check is open again. KeyError for an unknown judge."""
    fn = JUDGES[name]
    key = (name, fn)
    if key not in _HASHES:                      # reading the source of every judge for every stamp is too slow for a session start
        _HASHES[key] = hashlib.sha256('\n'.join(_judge_sources(fn)).encode()).hexdigest()[:16]
    return _HASHES[key]


def is_built(name):
    """False while a judge is still the stub that raises NotImplementedError."""
    return name in JUDGES and 'raise NotImplementedError' not in inspect.getsource(JUDGES[name])


def run_judge(rule, facts, params, ctx):
    """Run the judge of one rule and return its findings, complete and never silent.

    rule    a rule of the registry (gate_registry.rule) with a check that names a judge
    facts   the "facts" object of its probe, or None when the probe gave none
    params  flat values (gate_params.values)
    ctx     as described at the top; "rule" and "part" must be set

    No facts, a judge that is not built, or a judge that raises: one NOT_RUN finding that says so (a check that did not
    run has not passed). ctx["no_facts"], when set, is why the unit gave no facts (a unit that was given up as over
    budget): the row says that instead of the general text. Findings without "fix" get the rule's fix_order. "rule" is forced to the rule's id; "part" is
    kept when the judge set one (project and regression judges name the part each finding is about), else ctx's."""
    rid, part, name = rule['id'], ctx['part'], rule['check']['judge']
    if facts is None:
        why = ctx.get('no_facts') if isinstance(ctx.get('no_facts'), str) and ctx['no_facts'].strip() else 'the unit did not run or ended in an error'
        return [finding(rid, part, 'NOT_RUN', 'no facts from %s: %s' % (rule['check']['probe'], why), fix=rule.get('fix_order'))]
    if name not in JUDGES:
        return [finding(rid, part, 'NOT_RUN', 'judge %s does not exist' % name)]
    try:
        out = JUDGES[name](facts, params, dict(ctx, rule=rid))
    except NotImplementedError:
        return [finding(rid, part, 'NOT_RUN', 'judge %s is not built yet' % name)]
    except Exception as e:                       # a broken judge must never read as a pass
        return [finding(rid, part, 'NOT_RUN', 'judge %s failed: %s: %s' % (name, type(e).__name__, e))]
    done = []
    for f in out or []:
        f = dict(f, rule=rid)
        f['part'] = f.get('part') or part
        if not f.get('fix'):
            f['fix'] = list(rule.get('fix_order') or [])
        done.append(f)
    return done


# ---- private helpers (judge_hash covers the ones a judge reaches) -----------------------------------------------------

def _num(x):
    """Is x a real number a part can have? A bool, None, a text, NaN, infinity and anything from 1e9 up are not."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) < 1e9


def _list(x):
    """x when it is a list, else an empty one: facts are read, never trusted to have the right shape."""
    return x if isinstance(x, list) else []


def _g(x):
    """A number as short text: 2.5, 0.84, 12. '?' for anything that is not a number."""
    return '%g' % round(float(x), 2) if _num(x) else '?'


def _pct(x):
    """A share as text: 0.82 -> '82 %'."""
    return '%d %%' % round(100 * x) if _num(x) else '? %'


def _xyz(v):
    """Is v three real numbers?"""
    return isinstance(v, (list, tuple)) and len(v) == 3 and all(_num(c) for c in v)


def _anchor(a):
    """The anchor a probe wrote, normalised; None when it is not an anchor (a kind, three numbers, three numbers)."""
    if not isinstance(a, dict) or a.get('kind') not in gate_features.KINDS:
        return None
    axis, point = a.get('axis', (0, 0, 0)), a.get('point', (0, 0, 0))
    if not (_xyz(axis) and _xyz(point)):
        return None
    try:
        return gate_features.anchor(a['kind'], axis, point)
    except (TypeError, ValueError, OverflowError):
        return None


def _face(point, normal):
    """A "face" anchor from a point and a direction of the facts; None when they are not three numbers each."""
    return _anchor({'kind': 'face', 'axis': normal, 'point': point})


def _at(a):
    """Where an anchor is, as text: '(12, -30, 40)'."""
    if not a or a.get('kind') == 'part':
        return '(place not given)'
    return '(%s)' % ', '.join(_g(c) for c in a['point'])


def _not_run(ctx, text, anchor=None, part=None):
    """A NOT_RUN finding: the check could not decide, and why."""
    return finding(ctx['rule'], part or ctx['part'], 'NOT_RUN', text, anchor=anchor, context={'not_run': text})


def _rows(facts, key, ctx):
    """(the object rows of the list facts[key], findings). No such list, or rows that are not objects: NOT_RUN
    findings, so a probe that did not write the list never reads as "none found"."""
    v = facts.get(key) if isinstance(facts, dict) else None
    if not isinstance(v, list):
        return [], [_not_run(ctx, 'the facts hold no "%s" list: the probe did not look' % key)]
    good = [r for r in v if isinstance(r, dict)]
    if len(good) != len(v):
        return good, [_not_run(ctx, '%d of %d rows of "%s" cannot be read' % (len(v) - len(good), len(v), key))]
    return good, []


def _names(rows):
    """Sorted names of rows like {"name", "kind"}; a row without a name is called by its kind."""
    out = set()
    for r in _list(rows):
        if isinstance(r, dict):
            out.add(str(r.get('name') or str(r.get('kind') or 'unnamed body').replace('_', ' ')))
    return sorted(out)


def _table(ctx, name):
    """One table of ctx["tables"], {} when it is not there."""
    t = (ctx.get('tables') or {}).get(name)
    return t if isinstance(t, dict) else {}


def _basename(path):
    return str(path or '?').replace('\\', '/').rsplit('/', 1)[-1]


def _segments(row):
    """The "segments" of a census hole or a pocket as [{"d", "len"}], or None when they cannot be read."""
    segs = row.get('segments')
    if not isinstance(segs, list) or not segs:
        return None
    if not all(isinstance(s, dict) and _num(s.get('d')) and _num(s.get('len')) for s in segs):
        return None
    return segs


def _shape(segs):
    """Segments as text, from the mouth inwards: 'D7.5 x 0.54 + D4.4 x 1.5 + D3.6 x 5'. A cone shows both ends: 'D30>23 x 3.5'."""
    return ' + '.join('D%s%s x %s' % (_g(s['d']), '>%s' % _g(s['d2']) if _num(s.get('d2')) else '', _g(s['len'])) for s in segs or []) or 'shape not read'


def _hole_class(hole, ctx):
    """What a census hole is by TODAY's tables: probes._geom.classify_stack on its segments (with the names of what is
    coaxial, when the function takes them), so a corrected table row re-judges stored facts. While that function is not
    there (not built yet, or a copy of the tools without the probes), or the hole has no readable segments: the class
    the census stored.
    A blind hole that fits no row by itself and ends in a skin in front of a narrower coaxial hole of the same part
    (the census "behind": {"cap", "d"}, with a cap thinner than that hole is wide) is classified together with that
    hole: a counterbore, a one-layer bridge, then the clearance hole is that screw's hole. Only a screw counterbore or
    countersink is taken from such a pair, and its class also holds "behind_cap". Whether the skin may stay is the
    question of access.hole_closed, not of the class."""
    segs = _segments(hole)
    if segs and ctx.get('tables'):
        try:
            from probes import _geom
            classify = _geom.classify_stack
        except (ImportError, AttributeError):
            classify = None
        if classify is not None:
            names = [c['name'] for c in _list(hole.get('coaxial')) if isinstance(c, dict) and isinstance(c.get('name'), str)]

            def run(sg, through):
                try:
                    return classify(sg, through, ctx['tables'], names)
                except TypeError:                    # a classifier that takes no coaxial names
                    return classify(sg, through, ctx['tables'])
            try:
                c = run(segs, bool(hole.get('through')))
                behind = hole.get('behind')
                if (c is None and not hole.get('through') and isinstance(behind, dict) and _num(behind.get('d')) and _num(behind.get('cap'))
                        and behind['cap'] < behind['d'] < segs[-1]['d'] and segs[-1].get('d2') is None):
                    both = run(list(segs) + [{'d': behind['d'], 'len': max(behind['d'], 1.0)}], False)
                    if isinstance(both, dict) and both.get('table') == 'screws' and both.get('role') in ('counterbore', 'countersink'):
                        return dict(both, behind_cap=behind['cap'])
                return c
            except NotImplementedError:
                pass
    c = hole.get('class')
    return c if isinstance(c, dict) else None


def _piece_of(hole, holes):
    """The id of another hole of the same census that this hole is only a piece of, or None: both lie on one axis line,
    this one's whole length lies inside the other's, and its diameter is the other's diameter at that depth. A short
    cylinder at the bottom of a tapered seat comes out of the census as a hole of its own; it is the same surface.
    Positions and diameters are compared to 0.1 mm (an anchor is rounded to 0.05)."""
    segs = _segments(hole)
    a = hole.get('at') if isinstance(hole.get('at'), dict) else hole.get('anchor')
    if not segs or len(segs) != 1 or segs[0].get('d2') is not None or not isinstance(a, dict) or not (_xyz(a.get('point')) and _xyz(a.get('axis'))):
        return None
    ends = [a['point'], [a['point'][i] - a['axis'][i] * segs[0]['len'] for i in range(3)]]
    for o in holes:
        osegs = _segments(o) if isinstance(o, dict) and o is not hole else None
        b = (o.get('at') if isinstance(o.get('at'), dict) else o.get('anchor')) if osegs else None
        if not isinstance(b, dict) or not (_xyz(b.get('point')) and _xyz(b.get('axis'))):
            continue
        if abs(abs(sum(a['axis'][i] * b['axis'][i] for i in range(3))) - 1.0) > 1e-4:
            continue
        total, inside = sum(s['len'] for s in osegs), True
        if total <= segs[0]['len'] + _EPS:
            continue
        for e in ends:
            rel = [e[i] - b['point'][i] for i in range(3)]
            t = -sum(rel[i] * b['axis'][i] for i in range(3))                 # depth of this end below the other's mouth
            off = sum((rel[i] + t * b['axis'][i]) ** 2 for i in range(3)) ** 0.5
            if off > 0.1 or t < -0.1 or t > total + 0.1:
                inside = False
                break
            d_there, at = None, 0.0
            for s in osegs:                                                   # the other's diameter at that depth
                if t <= at + s['len'] + 0.1:
                    k = min(1.0, max(0.0, (t - at) / s['len'])) if s['len'] > 0 else 0.0
                    d2 = s.get('d2') if _num(s.get('d2')) else s['d']
                    near = [s['d'] + (d2 - s['d']) * k]
                    if t >= at + s['len'] - 0.1 and s is not osegs[-1]:
                        near.append(osegs[osegs.index(s) + 1]['d'])           # at a step both diameters meet
                    d_there = min(near, key=lambda d: abs(d - segs[0]['d']))
                    break
                at += s['len']
            if d_there is None or abs(d_there - segs[0]['d']) > 0.1:
                inside = False
                break
        if inside:
            return o.get('id') or '?'
    return None


def _pocket_off(segs, row, tol, late=False):
    """Where a measured stack leaves the pocket profile of one row of the inserts table: (a phrase that says it,
    measured, table value) of the first difference, diameters before depths, or None when nothing differs or the row
    cannot be read.
    The profile from the mouth inwards: an optional recess (one wider first step that is no relief of the row: a spot
    face; or a relief diameter over a smaller relief diameter), an optional melt relief (the row's diameter or one of its
    fallbacks), the lead-in, the hole (a tip hole below it counts as depth); depths in the row are from the relief
    floor. A row whose lead-in has no depth or is as wide as its hole is a straight hole: one segment.
    late (the table's "hole_short_by_relief"): a pocket with a relief may end one relief depth short of the row's depth.
    A hole that gets narrower before the insert ends is off too: the insert must end in the row's own hole diameter."""
    relief, lead, hole = row.get('relief') or {}, row.get('lead_in') or {}, row.get('hole') or {}
    if not (_num(lead.get('d')) and _num(lead.get('depth')) and _num(hole.get('d')) and _num(hole.get('depth'))):
        return None
    tol_d = tol.get('d') if _num(tol.get('d')) else 0.1
    tol_len = tol.get('depth') if _num(tol.get('depth')) else 0.3
    relief_d = [d for d in [relief.get('d')] + _list(relief.get('fallback_d')) if _num(d)]
    rest, step = list(segs), None

    def is_relief(s):
        return s.get('d2') is None and any(abs(s['d'] - d) <= tol_d for d in relief_d)
    if relief_d and len(rest) > 1 and rest[0].get('d2') is None and rest[0]['d'] >= min(relief_d) - tol_d:
        if not is_relief(rest[0]) or (len(rest) > 3 and is_relief(rest[1]) and rest[1]['d'] < rest[0]['d']):
            rest = rest[1:]                              # a recess: the pocket begins at its floor
    if is_relief(rest[0]):
        rest, step = rest[1:], rest[0]
    straight = lead['depth'] <= _EPS or abs(lead['d'] - hole['d']) <= tol_d
    want = [('hole', hole['d'])] if straight else [('lead-in', lead['d']), ('hole', hole['d'])]
    for i, (name, d) in enumerate(want):
        if i >= len(rest):
            return ('it has no D%s %s: the stack is still D%s there' % (_g(d), name, _g(segs[-1]['d'])), segs[-1]['d'], d)
        if abs(rest[i]['d'] - d) > tol_d:
            return ('its %s is D%s, the table says D%s' % (name, _g(rest[i]['d']), _g(d)), rest[i]['d'], d)
    if step is not None and _num(relief.get('depth')) and abs(step['len'] - relief['depth']) > tol_len:
        return ('its melt relief is %s deep, the table says %s' % (_g(step['len']), _g(relief['depth'])), step['len'], relief['depth'])
    if not straight and abs(rest[0]['len'] - lead['depth']) > tol_len:
        return ('its lead-in is %s deep, the table says %s' % (_g(rest[0]['len']), _g(lead['depth'])), rest[0]['len'], lead['depth'])
    own = rest[0] if straight else rest[1]
    tail = rest[1:] if straight else rest[2:]
    seat = row['insert_len'] - (0.0 if straight else lead['depth']) if _num(row.get('insert_len')) else None
    if seat is not None and late and step is not None:
        seat -= step['len']                              # a relief cut into a pocket that existed moved the insert down by its depth
    if tail and seat is not None and all(t['d'] < own['d'] for t in tail) and own['len'] < seat - tol_len:
        return ('its D%s hole is %s deep%s and gets narrower there (D%s): the insert is %s long and needs %s of it'
                % (_g(own['d']), _g(own['len']), '' if straight else ' below the lead-in', _g(tail[0]['d']), _g(row['insert_len']), _g(seat)), own['len'], seat)
    deep = sum(s['len'] for s in (rest if straight else rest[1:]))
    need = hole['depth'] - (0.0 if straight else lead['depth'])
    short = step['len'] if (late and step is not None) else 0.0          # a relief cut into a pocket that existed
    if deep < need - short - tol_len:
        hint = ' (%s under a relief that was cut later)' % _g(need - short) if short else ''
        if not short and step is not None and deep >= need - step['len'] - tol_len:
            # the usual cause, said once so that a pattern of such pockets is one group: a relief cut into a pocket that existed
            hint = ': one relief depth short (a relief that is cut into a pocket later moves the lead-in down and leaves the hole as it was)'
        return ('its hole is %s deep%s, the table says %s%s' % (_g(deep), '' if straight else ' below the lead-in', _g(need), hint), deep, need)
    return None


def _pocket_top(segs, row, tol):
    """mm from the mouth of a stack to where its pocket begins: the depth of a recess plus the depth of the melt
    relief, read as _pocket_off reads them. The insert ends flush with that level."""
    relief = row.get('relief') or {}
    tol_d = tol.get('d') if _num(tol.get('d')) else 0.1
    relief_d = [d for d in [relief.get('d')] + _list(relief.get('fallback_d')) if _num(d)]
    rest, top = list(segs), 0.0

    def is_relief(s):
        return s.get('d2') is None and any(abs(s['d'] - d) <= tol_d for d in relief_d)
    if relief_d and len(rest) > 1 and rest[0].get('d2') is None and rest[0]['d'] >= min(relief_d) - tol_d:
        if not is_relief(rest[0]) or (len(rest) > 3 and is_relief(rest[1]) and rest[1]['d'] < rest[0]['d']):
            top, rest = top + rest[0]['len'], rest[1:]
    if len(rest) > 1 and is_relief(rest[0]):
        top += rest[0]['len']
    return top


def _too_short(segs, row, tol):
    """Is a stack too short to hold the insert of one table row: less depth below its recess and relief than the
    insert is long? Such a hole gets ONE row, from inserts.floor ("the insert stands proud"); the shape and wall rules
    say nothing about it. False when the row has no insert length."""
    if not segs or not isinstance(row, dict) or not _num(row.get('insert_len')):
        return False
    return round(sum(s['len'] for s in segs) - _pocket_top(segs, row, tol), 4) < row['insert_len'] - _EPS


def _pocket(p, a):
    """A pocket of the fusion_inserts facts as text: 'pocket M3x4x5 at (12, -30, 40)'."""
    return 'pocket %s at %s' % (p.get('kind') or '(kind not given)', _at(a))


def _wall(p, key):
    """One wall record W of a pocket ("lead_in", "insert", "relief"), or None when it holds no number."""
    walls = p.get('walls')
    w = walls.get(key) if isinstance(walls, dict) else None
    return w if isinstance(w, dict) and _num(w.get('min')) else None


def _stops(w, need):
    """Names of what stops one thin wall from growing by `need` mm: the "blocked_by" entries whose gap is smaller than
    that (an entry without a gap counts). A neighbour that stands farther away than the wall has to grow stops nothing."""
    rows = [b for b in _list(w.get('blocked_by')) if isinstance(b, dict) and not (_num(b.get('gap')) and b['gap'] >= need - _EPS)]
    return _names(rows)


def _ray_short(p, limit, walls):
    """True when every wall given is at the cap of the probe's rays and that cap is under the limit: not looked far enough."""
    cap = (p.get('ray') or {}).get('max') if isinstance(p.get('ray'), dict) else None
    return _num(cap) and cap < limit - _EPS and all(w['min'] >= cap - _EPS for w in walls)


def _blocking(path, reach, min_share):
    """The hits of one path that are obstacles: not expected, inside the reach, covering at least `min_share` of the
    section. A hit without a readable distance or share counts."""
    out = []
    for h in _list(path.get('hits')):
        if not isinstance(h, dict) or h.get('expected') is True:
            continue
        if _num(h.get('at')) and h['at'] > reach + _EPS:
            continue
        if _num(h.get('share')) and h['share'] < min_share - _EPS:
            continue
        out.append(h)
    return out


_ACC_HOLES = ('hole', 'counterbore', 'insert_pocket')        # the "of" values of fusion_openings that are holes of the census


def _acc_s(p, o, u):
    """How far the point p lies along the unit axis u, counted from o."""
    return sum((p[i] - o[i]) * u[i] for i in range(3))


def _acc_off(p, o, u):
    """Distance of the point p from the line through o along the unit axis u."""
    s = _acc_s(p, o, u)
    return sum((p[i] - o[i] - s * u[i]) ** 2 for i in range(3)) ** 0.5


def _acc_path(o, key):
    """The path `key` of one opening row, or None when it was not measured."""
    paths = o.get('paths')
    path = paths.get(key) if isinstance(paths, dict) else None
    return path if isinstance(path, dict) and isinstance(path.get('hits'), list) else None


def _acc_label(o, word=None):
    """An opening row as text: 'hole h3 at (10, 0, 6)'; the far end of a through hole says so."""
    a = _anchor(o.get('anchor'))
    what = word or str(o.get('of') or 'opening').replace('_', ' ')
    return '%s %s%s at %s' % (what, o.get('feature') or '', ' (far end)' if o.get('end') == 'exit' else '', _at(a))


def _acc_overlay(params, ctx, out):
    """The compiled parameter access.overlay_pattern, or None when it is not set. A pattern that does not compile is one
    NOT_RUN finding in `out` and leaves no body out."""
    pat = params.get('access.overlay_pattern')
    if not isinstance(pat, str) or not pat.strip():
        return None
    try:
        return re.compile(pat)
    except re.error as e:
        out.append(_not_run(ctx, 'the parameter access.overlay_pattern is not a regular expression (%s): no body was left out' % e))
        return None


def _acc_sorted(path, reach, min_share, overlay):
    """The obstacles of one path (_blocking), sorted by what they prove:
      "self"     the part itself, measured: its own material stands in the path
      "body"     another solid body, measured: whether it is in the way depends on the assembly order
      "approx"   a mesh body, a body whose boolean failed, a bounding box: not a measurement, so never a FAIL
      "overlay"  a body the owner named in access.overlay_pattern (an old version laid over the model): not counted"""
    out = {'self': [], 'body': [], 'approx': [], 'overlay': []}
    for h in _blocking(path, reach, min_share):
        approx = bool(h.get('approx') or h.get('by_box') or h.get('by_points') or h.get('kind') == 'mesh')
        if h.get('kind') == 'self':
            out['approx' if approx else 'self'].append(h)
        elif overlay is not None and overlay.search(str(h.get('name') or '')):
            out['overlay'].append(h)
        else:
            out['approx' if approx else 'body'].append(h)
    return out


def _acc_first(hits):
    """mm from the mouth to the nearest of these hits; 0.0 when none gives a distance."""
    return min([h['at'] for h in hits if _num(h.get('at'))] or [0.0])


def _acc_short(path, reach, label, r_min=None):
    """The text of a NOT_RUN finding when a path with no obstacle was followed for less than `reach` or was measured
    narrower than `r_min` (nothing found is not "not looked that far"), else None."""
    if not _num(path.get('reach')) or path['reach'] < reach - _EPS:
        return '%s: the path was followed for %s mm, the rule needs %s' % (label, _g(path.get('reach')), _g(reach))
    if r_min is not None and (not _num(path.get('r')) or path['r'] < r_min - _EPS):
        return '%s: the path was measured %s mm wide, the rule needs %s' % (label, _g(2 * path['r']) if _num(path.get('r')) else '?', _g(2 * r_min))
    return None


def _acc_bores(openings, ctx, cap, out):
    """The opening rows of the fusion_openings facts, put together into bores.

    Census holes that lie on one axis and follow each other within `cap` mm are ONE bore: a counterbore the census
    listed apart from its hole, a thin ring at the far end of a stepped bore, two holes with a thin cap between them
    (access.hole_closed speaks about the cap). Only the two outer ends of a bore are openings to the outside: a row at
    an inner end (the step between two diameters) is not judged. Without a census in ctx every hole is a bore of its
    own, with every row it has.
    Returns (bores, loose):
      bores  [{"ids": [hole ids], "holes": [census holes], "ends": [the rows at its outer ends, in the order of the
              facts], "origin", "axis", "lo", "hi"}]; "holes" is empty and the line is None without a census
      loose  the rows that are no hole of the census: slots, windows, screw heads, kinds nobody knows
    A hole row whose feature the census does not hold is a NOT_RUN finding in `out`."""
    census = ctx.get('census') if isinstance(ctx.get('census'), dict) else None
    loose = [o for o in openings if o.get('of') not in _ACC_HOLES]
    rows = [o for o in openings if o.get('of') in _ACC_HOLES]
    if census is None:
        by_id = {}
        for o in rows:
            by_id.setdefault(str(o.get('feature')), []).append(o)
        return [{'ids': [k], 'holes': [], 'ends': by_id[k], 'origin': None, 'axis': None, 'lo': None, 'hi': None} for k in sorted(by_id)], loose
    holes = {h.get('id'): h for h in _list(census.get('holes')) if isinstance(h, dict) and isinstance(h.get('id'), str)}
    groups, alone = [], []
    for hid in sorted(holes):
        h, a = holes[hid], _anchor(holes[hid].get('anchor'))
        depth = h.get('depth') if _num(h.get('depth')) else sum(s['len'] for s in _segments(h) or [])
        if a is None or not any(a['axis']):
            alone.append(hid)
            continue
        for g in groups:
            dot = sum(a['axis'][i] * g['axis'][i] for i in range(3))
            if abs(dot) >= 0.9998 and _acc_off(a['point'], g['origin'], g['axis']) <= 0.15:
                s = _acc_s(a['point'], g['origin'], g['axis'])
                e = s - (depth if dot > 0 else -depth)
                g['items'].append((min(s, e), max(s, e), hid))
                break
        else:
            groups.append({'origin': a['point'], 'axis': a['axis'], 'items': [(-depth, 0.0, hid)]})
    bores, where = [], {}
    for g in groups:
        cur = None
        for lo, hi, hid in sorted(g['items']):
            if cur is not None and lo <= cur['hi'] + cap + _EPS:
                cur['hi'] = max(cur['hi'], hi)
                cur['ids'].append(hid)
            else:
                cur = {'ids': [hid], 'holes': [], 'ends': [], 'origin': g['origin'], 'axis': g['axis'], 'lo': lo, 'hi': hi}
                bores.append(cur)
            where[hid] = cur
    for hid in alone:
        where[hid] = {'ids': [hid], 'holes': [], 'ends': [], 'origin': None, 'axis': None, 'lo': None, 'hi': None}
        bores.append(where[hid])
    for b in bores:
        b['holes'] = [holes[i] for i in b['ids']]
    for o in rows:
        b, a = where.get(o.get('feature') if isinstance(o.get('feature'), str) else None), _anchor(o.get('anchor'))
        if b is None:
            out.append(_not_run(ctx, '%s: the hole census of this part does not hold it, so what it is for is not known' % _acc_label(o), a))
            continue
        if b['axis'] is None or a is None:
            b['ends'].append(o)
            continue
        s, dot = _acc_s(a['point'], b['origin'], b['axis']), sum(a['axis'][i] * b['axis'][i] for i in range(3))
        if (dot > 0 and s >= b['hi'] - 0.1) or (dot < 0 and s <= b['lo'] + 0.1):
            b['ends'].append(o)
    return bores, loose


def _acc_no_end(bore, ctx):
    """The NOT_RUN finding for a bore of the census that has no opening row at an outer end: the facts hold no row for
    its holes, or only rows that lie inside it or look the other way. Nothing in front of such a hole was measured,
    and silence would read as "free"."""
    hole = bore['holes'][0] if bore['holes'] else {}
    a = _anchor(hole.get('anchor'))
    return _not_run(ctx, 'hole %s %s at %s: these facts hold no opening row at an outer end of it, so what stands in front of it was not measured: collect the facts of this part again'
                    % (' + '.join(str(i) for i in bore['ids']), _shape(_segments(hole)), _at(a)), a)


def _acc_use(bore, ctx, params):
    """What a bore is for, by the classes of its census holes (today's tables): ("seat", hole, class) when one of
    them is a bearing seat, ("screw", hole, class) when one is a hole of the screws table or a pilot of the inserts
    table, else (None, None, None): a hole of no table, an insert pocket alone, a pocket for a bought part
    (_bought_pocket: a magnet pocket can have the diameter of a tap drill)."""
    found = {}
    for h in bore['holes']:
        c = _hole_class(h, ctx)
        if not isinstance(c, dict) or _bought_pocket(h, params, ctx):
            continue
        if c.get('table') == 'bearings' and c.get('role') == 'bearing_seat':
            found.setdefault('seat', (h, c))
        elif c.get('table') == 'screws' or (c.get('table') == 'inserts' and c.get('role') == 'pilot'):
            found.setdefault('screw', (h, c))
    for use in ('seat', 'screw'):
        if use in found:
            return (use,) + found[use]
    return None, None, None


def _acc_note(groups, kind, hits, label):
    """Remember, per covering component, which opening it was found in front of and how far out.
    A covering component is named by the component of the hit (the text before " / " in its name). A measured solid
    body that is no printed part and lies inside an assembly (the hit's "in": the components above its own, outermost
    first, written by the opening unit) is named with that path: "Gearbox / Motor / Rotor", because "Rotor" alone
    tells the owner nothing. A printed part keeps the name its part id is made from; mesh bodies and overlays keep
    the name the overlay pattern is matched against."""
    for h in hits:
        name = str(h.get('name') or str(h.get('kind') or 'unnamed body').replace('_', ' ')).split(' / ')[0]
        where = h.get('in')
        if kind == 'body' and isinstance(where, str) and where.strip() and not h.get('part'):
            name = '%s / %s' % (where.strip(), name)
        groups[kind].setdefault(name, []).append((label, h.get('at')))


def _acc_grouped(ctx, groups, limit, what, shown=6, places=2, brief=6):
    """The findings that are about covering bodies, not about one opening.
      measured solid bodies   ONE row for the part and the rule, FAIL with ask = "assembly.order": every covering
                              component nearest first, each with its distance and the openings it stands in front
                              of. One question, answered once: free only if each of them is mounted later and comes
                              off first. The text names the nearest `shown` covers with up to `places` openings
                              each, the next `brief` with their distance only, and counts the rest; "by" holds
                              every cover, nearest first, and the question lists them all. measure = the nearest
                              distance.
      a mesh or a failed cut  NOT_RUN, one row per body: an approximation proves nothing, and the text says the one
                              thing to do
      a named overlay         one INFO row for all of them: not counted, and said
    anchor = the part. The context of the asked row is every covering component with the openings it stands in
    front of, and no distance: the row keeps its id (and an answer or a waiver holds) while the same components
    stand in front of the same openings, however far out. It gets a new id, and is asked again with the whole list,
    when a component joins or leaves, and when a component comes to stand in front of another opening or leaves one:
    covers are named by component, a component can hold many bodies (a bundle of screws), and an answer given while
    one of them stood far out in front of one head says nothing about another of them right over another head. An
    answer never covers a cover, or a place of a cover, it did not name."""
    out = []
    if groups['body']:
        covers = sorted(((min([r[1] for r in rows if _num(r[1])] or [0.0]), name, sorted({r[0] for r in rows})) for name, rows in groups['body'].items()),
                        key=lambda c: (c[0], c[1]))
        first, names = covers[0][0], [c[1] for c in covers]
        n_open = len({label for c in covers for label in c[2]})
        if len(covers) == 1:
            labels = covers[0][2]
            text = ('%s stands in front of %d %s of this part, the nearest %s mm out (%s): free only if it is mounted later and comes off first'
                    % (names[0], len(labels), what, _g(first), '; '.join(labels[:6]) + ('; ...' if len(labels) > 6 else '')))
        else:
            one = lambda c: '%s %s mm out (%s%s)' % (c[1], _g(c[0]), '; '.join(c[2][:places]), '; and %d more' % (len(c[2]) - places) if len(c[2]) > places else '')
            rest, left = covers[shown:shown + brief], len(covers) - shown - brief
            text = ('%d other bodies stand in front of %d %s of this part, the nearest %s mm out. Nearest first: %s%s%s. Free only if each of them is mounted later and comes off first'
                    % (len(covers), n_open, what, _g(first), '; '.join(one(c) for c in covers[:shown]),
                       '; farther out: %s' % ', '.join('%s %s mm' % (c[1], _g(c[0])) for c in rest) if rest else '',
                       '; and %d more (the question names them all)' % left if left > 0 else ''))
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text, measure=first, limit=limit, by=names,
                           context={'covers': sorted([c[1], c[2]] for c in covers), 'of': what}, ask='assembly.order'))
    for name in sorted(groups['approx']):
        rows = groups['approx'][name]
        labels = sorted({r[0] for r in rows})
        first = min([r[1] for r in rows if _num(r[1])] or [0.0])
        out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                           '%s lies in front of %d %s of this part, the nearest %s mm out (%s), and it is known only as a mesh or by sample points: '
                           'not a measurement. Make it a solid body, or, when it is no part of the build (an old version, a scan), let the owner name it in access.overlay_pattern'
                           % (name, len(labels), what, _g(first), '; '.join(labels[:6]) + ('; ...' if len(labels) > 6 else '')),
                           measure=first, limit=limit, by=[name], context={'approx': name, 'in_front_of': labels}))
    if groups['overlay']:
        names = sorted(groups['overlay'])
        n = sum(len(groups['overlay'][k]) for k in names)
        out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                           '%d hits by bodies the owner named as overlays (access.overlay_pattern) were not counted: %s' % (n, ', '.join(names)),
                           by=names, context={'overlay': names, 'hits': n}))
    return out


def _regions(facts, ctx, slit):
    """(the regions of the stl_walls facts that are slits (`slit` True) or walls, each with a number in "min"; findings)."""
    regions, out = _rows(facts, 'regions', ctx)
    good = []
    for r in regions:
        if (r.get('kind') == 'slit') != slit:
            continue
        if not _num(r.get('min')):
            out.append(_not_run(ctx, 'a %s region has no thickness' % ('slit' if slit else 'wall'), _face(r.get('point'), r.get('normal'))))
            continue
        good.append(r)
    return good, out


def _region_text(r):
    a = _face(r.get('point'), r.get('normal'))
    return a, 'at %s over %s mm2' % (_at(a), _g(r.get('area')))


def _screw(s, a):
    """A screw of the fusion_screws facts as text."""
    return 'screw %s at %s' % (s.get('ref') or '%s x %s' % (s.get('size'), _g(s.get('length'))), _at(a))


def _bearing(b, a):
    des = b.get('designation')
    return 'bearing %s%s at %s' % (b.get('ref') or '', ' (%s)' % des if des else '', _at(a))


def _bearing_named_wrong(b, ctx):
    """True for a row of the bearing facts that rests on its name alone ("by": "name") while its measured d, D and B
    contradict the table row of that name: a clamp screw named after the bearing it clamps. Such a body is no bearing,
    whatever it is called. A name with no measured ring (the probe then copies the row's numbers) contradicts nothing."""
    if b.get('by') != 'name':
        return False
    table = _table(ctx, 'bearings')
    sizes, des = table.get('sizes') or {}, b.get('designation')
    if not isinstance(des, str):
        return False
    alias = (table.get('aliases') or {}).get(des)
    row = sizes.get(des) or (sizes.get(alias) if isinstance(alias, str) else None)
    if not isinstance(row, dict) or not all(_num(b.get(k)) and _num(row.get(k)) for k in ('d', 'D', 'B')):
        return False
    tol = (table.get('tol') or {}).get('d')
    tol = tol if _num(tol) else 0.05
    return any(abs(b[k] - row[k]) > tol + _EPS for k in ('d', 'D', 'B'))


def _bearing_off_row(b, ctx):
    """For a row that _bearing_named_wrong() refuses: is it still that bearing, drawn off its table row? True when two
    of its three measures (d, D, B) are the row's within the table tolerance: a 608 drawn 7.3 wide. Then the body is a
    ring nobody judged, not "something else". A clamp screw named after the bearing (3 x 12 x 8 against 8 x 22 x 7)
    matches in none."""
    table = _table(ctx, 'bearings')
    sizes, des = table.get('sizes') or {}, b.get('designation')
    alias = (table.get('aliases') or {}).get(des) if isinstance(des, str) else None
    row = (sizes.get(des) if isinstance(des, str) else None) or (sizes.get(alias) if isinstance(alias, str) else None)
    if not isinstance(row, dict):
        return False
    tol = (table.get('tol') or {}).get('d')
    tol = tol if _num(tol) else 0.05
    return sum(1 for k in ('d', 'D', 'B') if _num(b.get(k)) and _num(row.get(k)) and abs(b[k] - row[k]) <= tol + _EPS) >= 2


def _bearing_ring_parts(b, ring, gap_max):
    """The printed parts one ring of a bearing belongs to (part ids): the part that seats it, every printed cover that
    touches one of its two faces (within the contact gap), and, for a face nothing touches, the nearest printed cover."""
    parts = set()
    seat = b.get('seat').get(ring) if isinstance(b.get('seat'), dict) else None
    if isinstance(seat, dict) and seat.get('part'):
        parts.add(str(seat['part']))
    r = b.get('rings').get(ring) if isinstance(b.get('rings'), dict) else None
    for f in _list(r.get('faces') if isinstance(r, dict) else None):
        covers = [c for c in _list(f.get('covered') if isinstance(f, dict) else None) if isinstance(c, dict) and _num(c.get('gap'))]
        near = [c for c in covers if c['gap'] <= gap_max + _EPS]
        for c in near or sorted(covers, key=lambda c: c['gap'])[:1]:
            if c.get('part'):
                parts.add(str(c['part']))
    return parts


def _bearing_unheld(b, gap_max):
    """True when the facts PROVE that this bearing is held by bought bodies and that no printed part has anything to do
    with it: no ring has a seat in a printed part, NO printed cover is listed at any of its four ring faces (at any
    distance inside the unit's look: a printed lip that stops short of a ring is a finding of that part, not "merely
    near"), no clamp row names a printed part, and at least one ring face is touched (within `gap_max`) by a body
    that is not printed, or a ring is seated in one. Such a bearing sits inside a bought assembly (a roller on a
    steel post between steel washers): a printed part that is only screwed to that assembly does not answer for it.
    Proven means measured: "seat" holds both rings (None = no seat found), every ring face has its "covered" list,
    and "clamps" is a list. Anything missing: False, and the bearing is judged as before (a bearing without its
    holding data is NOT_RUN, never left out). A bearing that nothing touches at all is not held by a bought assembly
    either: False, it stays a finding of every part near it."""
    seat, rings = b.get('seat'), b.get('rings')
    if not isinstance(seat, dict) or not isinstance(rings, dict) or not isinstance(b.get('clamps'), list):
        return False
    printed = lambda c: c.get('kind') == 'printed' or bool(c.get('part'))
    steel = False
    for ring in ('inner', 'outer'):
        if ring not in seat or not (seat[ring] is None or isinstance(seat[ring], dict)):
            return False
        if isinstance(seat[ring], dict):
            if printed(seat[ring]):
                return False
            steel = True
        r = rings.get(ring)
        faces = {f['side']: f for f in _list(r.get('faces') if isinstance(r, dict) else None) if isinstance(f, dict) and isinstance(f.get('side'), str)}
        for side in ('+', '-'):
            covered = faces[side].get('covered') if side in faces else None
            if not isinstance(covered, list) or not all(isinstance(c, dict) for c in covered):
                return False
            for c in covered:
                if printed(c):
                    return False                     # a printed body at a ring face, touching or standing off: that part answers for the face
                steel = steel or (_num(c.get('gap')) and c['gap'] <= gap_max + _EPS)
    for c in b['clamps']:
        if not isinstance(c, dict) or c.get('part') or c.get('stop_part'):
            return False
    return steel


def _bearing_mine(b, part, gap_max):
    """{"inner": bool, "outer": bool}: the rings of a bearing that are judged for this part. A ring is judged where it
    is seated or held (_bearing_ring_parts): the outer ring of a bearing in a neighbour's housing is the neighbour's.
    A ring no printed part holds goes with the part that holds the other ring.
    A bearing that bought bodies hold and no printed part does (_bearing_unheld: proven by the facts) is judged for
    no part: bearings.holding counts it in one INFO row. A bearing whose facts cannot prove that (no seat data, a ring
    face without its covers), and one that nothing touches at all, is reported for every part near it when no
    printed part is known to hold it, so it is never silent."""
    if _bearing_unheld(b, gap_max):
        return {'inner': False, 'outer': False}
    own = {ring: _bearing_ring_parts(b, ring, gap_max) for ring in ('inner', 'outer')}
    if not own['inner'] and not own['outer']:
        return {'inner': True, 'outer': True}
    mine = {ring: part in own[ring] for ring in own}
    if any(mine.values()):
        for ring in own:
            if not own[ring]:
                mine[ring] = True
    return mine


def _bearing_twin(c, b, bearings):
    """Is a cover the other bearing of a pair: a bearing with the same bore and outer diameter, so its inner ring bears
    on the inner ring and its outer ring on the outer ring? The probe says so ("twin"); facts of an older probe are
    read by the name: the cover carries the ref of another bearing of the list, copy number dropped."""
    if 'twin' in c:
        return bool(c.get('twin'))
    if c.get('kind') != 'hardware' or not isinstance(c.get('name'), str):
        return False
    base = lambda n: re.sub(r'\s*(?:#\d+|\(\d+\)|:\d+)\s*$', '', n) if isinstance(n, str) else None
    for o in bearings:
        if o is not b and base(o.get('ref')) == base(c['name']) and all(_num(o.get(k)) and _num(b.get(k)) and abs(o[k] - b[k]) <= 0.1 for k in ('d', 'D')):
            return True
    return False


def _clamp_gaps(b, c):
    """(air between the ring and its clamp, air between the ring and the stop on the other face) of one clamp row, mm;
    None for a gap the facts do not hold. Read from the row ("gap", "stop_gap"), else from the covers of the ring faces
    by name (facts of an older probe)."""
    if _num(c.get('gap')) and _num(c.get('stop_gap')):
        return c['gap'], c['stop_gap']
    r = b.get('rings').get(c.get('ring')) if (isinstance(b.get('rings'), dict) and isinstance(c.get('ring'), str)) else None
    faces = {f.get('side'): _list(f.get('covered')) for f in _list(r.get('faces') if isinstance(r, dict) else None)
             if isinstance(f, dict) and isinstance(f.get('side'), str)}
    def gap(side, name):
        hit = [x.get('gap') for x in faces.get(side) or [] if isinstance(x, dict) and x.get('name') == name and _num(x.get('gap'))]
        return min(hit) if hit else None
    side = c.get('side') if isinstance(c.get('side'), str) else None
    return gap(side, c.get('name')), gap('-' if side == '+' else '+', c.get('against'))


def _on_axis(a, b, tol=0.6):
    """Do two anchors share an axis: directions parallel within about 8 degrees (either way), and the point of `b`
    within `tol` mm of the line of `a`?"""
    if not a or not b:
        return False
    ax, bx, p, q = a['axis'], b['axis'], a['point'], b['point']
    na, nb = sum(x * x for x in ax) ** 0.5, sum(x * x for x in bx) ** 0.5
    if na < 0.5 or nb < 0.5 or abs(sum(x * y for x, y in zip(ax, bx))) < 0.99 * na * nb:
        return False
    d = [q[i] - p[i] for i in range(3)]
    t = sum(d[i] * ax[i] for i in range(3)) / na
    off = sum((d[i] - t * ax[i] / na) ** 2 for i in range(3)) ** 0.5
    return off <= tol


def _slot_under(w, ctx, tol=0.3):
    """The census opening of shape "slot" a washer sits over, or None: the slot's mouth lies in the washer's landing
    plane, its long axis runs right through under the washer, and the slot is narrower than the washer.
    Returns {"id", "length", "width"}."""
    a = _anchor(w.get('anchor'))
    census = ctx.get('census') if isinstance(ctx.get('census'), dict) else {}
    if not a or not _num(w.get('od')):
        return None
    for o in _list(census.get('openings')):
        size, oa = o.get('size') if isinstance(o, dict) else None, _anchor(o.get('anchor') if isinstance(o, dict) else None)
        if not (isinstance(o, dict) and o.get('shape') == 'slot' and isinstance(size, list) and len(size) == 2 and all(_num(x) for x in size) and oa):
            continue
        length, width = max(size), min(size)
        d = [a['point'][i] - oa['point'][i] for i in range(3)]
        up = sum(d[i] * oa['axis'][i] for i in range(3))
        flat = max(0.0, sum(x * x for x in d) - up * up) ** 0.5
        parallel = abs(sum(x * y for x, y in zip(a['axis'], oa['axis']))) >= 0.99
        if parallel and abs(up) <= tol and flat <= (length - width) / 2.0 + tol and width < w['od']:
            return {'id': o.get('id'), 'length': length, 'width': width}
    return None


def _bridges_slot(w, land, ctx, land_min):
    """Does a washer whose ring is not fully on material span a slot of the part and bear on both of its flanks?
    True when the census lists a slot under it (_slot_under), the washer reaches at least `land_min` mm over each flank
    (half of its outer diameter less the slot width: the parameter washers.slot_land_min), and every sample circle of
    the washer ring is on material over at least the share that lies beside that slot (_slot_shares), less two of the
    24 sample points of a circle (where the four edges of the slot fall between the points). A washer that hangs over
    an edge of the part, that has lost one flank, or that is hardly wider than the slot measures less and is no bridge:
    over a slot as wide as itself every expected share is 0, and "0 of 0" must not read as a pass."""
    shares = land.get('ring_shares')
    if not (isinstance(shares, list) and shares and all(_num(x) for x in shares) and _num(w.get('id_d')) and _num(w.get('od')) and 0 < w['id_d'] < w['od']):
        return False
    slot = _slot_under(w, ctx)
    if slot is None or not _num(land_min) or (w['od'] - slot['width']) / 2.0 < land_min - _EPS:
        return False
    need = _slot_shares(w, slot, len(shares))
    return all(have >= want - 2.0 / 24.0 - _EPS for have, want in zip(shares, need))


def _slot_shares(w, slot, n=6):
    """The share of each of the probe's `n` sample circles of a washer ring that lies beside a straight slot of the given
    width running under the washer's centre, innermost circle first (the circles of fusion_screws: between
    id + 0.15 ring and od - 0.05 ring). What a washer that bears on both flanks of the slot measures."""
    import math
    r_in, r_out = w['id_d'] / 2.0, w['od'] / 2.0
    ring = r_out - r_in
    lo, hi = r_in + 0.15 * ring, r_out - 0.05 * ring
    out = []
    for j in range(n):
        r = lo + (hi - lo) * (j + 1) / (n + 1.0)
        out.append(1.0 - (2.0 / math.pi) * math.asin(min(1.0, max(0.0, slot['width']) / 2.0 / r)) if r > _EPS else 0.0)
    return out


def _project_part(facts, row, name):
    """The part a project finding is about: the row's part id, else "<project key>:<object or file name>"."""
    part = row.get('part')
    return part if isinstance(part, str) and part else '%s:%s' % (facts.get('project') or '?', name)


def _objects(facts, ctx):
    """(the objects of a project_consistency facts object, findings). A project file that does not exist is NOT_RUN:
    an empty object list of a missing file is not "every object is fine"."""
    if isinstance(facts, dict) and facts.get('exists') is False:
        return [], [_not_run(ctx, 'the project file %s does not exist: nothing was compared' % facts.get('file'))]
    return _rows(facts, 'objects', ctx)


def _stamp_state(o):
    s = o.get('stamp')
    return s.get('state') if isinstance(s, dict) else None


# ---- inserts (probes fusion_census, fusion_inserts, fusion_openings) ----------------------------------------------------------

def _fit_keys(value):
    """The setting keys out of the parameter target.fit_settings ("a b c", commas allowed)."""
    return list(dict.fromkeys(str(value or '').replace(',', ' ').split()))


def _declared(target, key):
    """The value the declared target gives a fit setting: under its own key, the nozzle diameter under "nozzle"."""
    if not isinstance(target, dict):
        return None
    return target.get({'nozzle_diameter': 'nozzle'}.get(key, key))


def _same_setting(a, b):
    """Are two setting values the same? Numbers to 0.0005, texts without regard to case, lists item by item."""
    if _num(a) and _num(b):
        return abs(a - b) <= 5e-4
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same_setting(x, y) for x, y in zip(a, b))
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b and a is not None


def _val(x):
    """A setting value as text: 0.125, classic, 'not set'."""
    if x is None:
        return 'not set'
    if isinstance(x, list):
        return '[%s]' % ', '.join(_val(v) for v in x)
    return ('%g' % round(float(x), 4)) if _num(x) else str(x)


def _file_part(facts):
    return '%s:%s' % (facts.get('project') or '?', _basename(facts.get('file')))


def _bought_pocket(hole, params, ctx):
    """(kind, row of the pockets table) when a census hole is a round pocket for a bought part, else None: it is
    blind, it is not an exact insert pocket or bearing seat, its deepest diameter is the value of the row's "d"
    parameter within the table's tolerance, and it is not deeper than depth_max_factor times the row's "depth"
    parameter. A screw row does not stand in the way: a magnet pocket can have the diameter of a tap drill, and a
    hole that shallow holds no thread. A row whose parameters were not handed to the judge matches nothing.
    A hole that holds an insert or a screw is NOT such a pocket, whatever its diameter: a bought or hardware body on
    its axis whose own name (the part behind " / ") matches inserts.ref_pattern or screws.ref_pattern. An insert pocket
    that has the diameter of the magnet pocket is then judged by the insert rules. The name only takes the skip away,
    so a wrong name costs a look, never a pass."""
    segs = _segments(hole)
    if not segs or hole.get('through'):
        return None
    c = _hole_class(hole, ctx)
    if c and c.get('match') == 'exact' and c.get('table') in ('inserts', 'bearings'):
        return None
    own = [str(x['name']).rsplit(' / ', 1)[-1] for x in _list(hole.get('coaxial'))
           if isinstance(x, dict) and x.get('kind') in ('bought', 'hardware') and isinstance(x.get('name'), str)]
    for pattern in (params.get('inserts.ref_pattern'), params.get('screws.ref_pattern')):
        if isinstance(pattern, str) and _named_screw(own, pattern):
            return None                              # an insert or a screw sits in it: no pocket for a bought part
    table = _table(ctx, 'pockets')
    tol = (table.get('tol') or {}).get('d') if isinstance(table.get('tol'), dict) else None
    factor = table.get('depth_max_factor')
    kinds = table.get('kinds') if isinstance(table.get('kinds'), dict) else {}
    depth = hole.get('depth') if _num(hole.get('depth')) else sum(s['len'] for s in segs)
    d = segs[-1]['d']
    for kind in sorted(kinds):
        row = kinds[kind]
        if not isinstance(row, dict):
            continue
        want_d, want_depth = params.get(row.get('d')), params.get(row.get('depth'))
        if not (_num(want_d) and _num(want_depth) and _num(tol)):
            continue
        if abs(d - want_d) <= tol + _EPS and depth <= (factor if _num(factor) else 2.0) * want_depth + _EPS:
            return kind, row
    return None


@judge
def inserts_pocket_shape(facts, params, ctx):
    """Rule inserts.pocket_shape. Reads fusion_census facts: "holes"[].segments, .class, .anchor, .through.
    A hole whose class has table "inserts" and a match other than "exact" (diameters in the band of an insert kind,
    profile not the table's) is FAIL: measure = the diameter (or depth) that is off, limit = the table value,
    by = [insert kind]; the text names the whole stack.
    The class is recomputed from the segments with ctx["tables"] (probes._geom.classify_stack) so that a corrected
    table re-judges stored facts; the class the census stored is used only while that function is not built.
    A hole with neither readable segments nor a class: NOT_RUN.
    A blind hole that is a pocket of the pockets table (_bought_pocket: its diameter is the value of that kind's "d"
    parameter) is not judged here: a magnet pocket can have the diameter of an insert pocket. Parameters: the "d" and
    "depth" parameters of every kind of the pockets table.
    Not a row here either: a hole that is only a piece of another hole of the census (_piece_of), and a hole too short
    to hold the insert of its kind (_too_short): inserts.floor has the one row for that hole.
    The table's "hole_short_by_relief" is read by the classifier and by _pocket_off. It is false as shipped: a pocket
    that ends one relief depth short of its row (its relief was cut later) is a row here, and its text names that
    cause. With true such a pocket is exact."""
    holes, out = _rows(facts, 'holes', ctx)
    table = _table(ctx, 'inserts')
    tol, late = table.get('tol') or {}, bool(table.get('hole_short_by_relief'))
    for h in holes:
        a, segs = _anchor(h.get('anchor')), _segments(h)
        if segs is None and not isinstance(h.get('class'), dict):
            out.append(_not_run(ctx, 'hole %s at %s: neither its segments nor its class can be read' % (h.get('id') or '', _at(a)), a))
            continue
        c = _hole_class(h, ctx)
        if not c or c.get('table') != 'inserts' or c.get('match') == 'exact':
            continue
        if _bought_pocket(h, params, ctx):           # a pocket for a bought part at its parameter's size: fits.bought_part_pocket judges it
            continue
        kind = c.get('kind')
        row = (table.get('kinds') or {}).get(kind) if isinstance(kind, str) else None
        if _piece_of(h, holes) or _too_short(segs, row, tol):
            continue
        off = _pocket_off(segs, row, tol, late) if segs and isinstance(row, dict) else None
        text = 'hole %s at %s is in the band of insert %s and is not its pocket' % (_shape(segs), _at(a), kind)
        if off:
            text += ': %s' % off[0]
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text, anchor=a, measure=off[1] if off else None,
                           limit=off[2] if off else None, by=[str(kind)],
                           context={'kind': kind, 'segments': [[s['d'], s['len']] for s in segs or []], 'through': bool(h.get('through'))}))
    return out


def _census_hole(p, ctx):
    """The census hole of one pocket of the fusion_inserts facts (its "hole" id in ctx["census"]), or None."""
    census = ctx.get('census')
    for h in _list(census.get('holes')) if isinstance(census, dict) else []:
        if isinstance(h, dict) and h.get('id') is not None and h.get('id') == p.get('hole'):
            return h
    return None


def _not_a_pocket(p, params, ctx):
    """Why the insert rules do not judge one pocket of the fusion_inserts facts, or None when they do:
    "bought"  its census hole is a pocket for a bought part at the size of its parameter (_bought_pocket: a magnet
              pocket can have the diameter of an insert pocket); fits.bought_part_pocket judges it
    "short"   the hole is too short to hold the insert of its kind (_too_short with the relief and recess the probe
              named): the levels the walls are measured at do not exist in it. inserts.floor has the one row for it."""
    h = _census_hole(p, ctx)
    if h is not None and _bought_pocket(h, params, ctx):
        return 'bought'
    table = _table(ctx, 'inserts')
    row = (table.get('kinds') or {}).get(p.get('kind')) if isinstance(p.get('kind'), str) else None
    segs = _segments(p)
    if segs and isinstance(row, dict) and _num(row.get('insert_len')):
        top = sum(x['depth'] for x in (p.get('relief'), p.get('recess')) if isinstance(x, dict) and _num(x.get('depth')))
        if round(sum(s['len'] for s in segs) - top, 4) < row['insert_len'] - _EPS:
            return 'short'
    return None


def _stop_text(walls, named):
    """The names of what stops a wall, for the text of a finding: sorted, at most three, then how many more (the same
    cause gives the same text on every pocket of a pattern). A body whose light bulb is off in the document is marked
    "(hidden)"."""
    order = sorted(named)
    hidden = {str(b.get('name')) for w in walls for b in _list(w.get('blocked_by')) if isinstance(b, dict) and b.get('hidden') is True}
    shown = ['%s%s' % (n, ' (hidden)' if n in hidden else '') for n in order[:3]]
    return ', '.join(shown) + (' and %d more' % (len(order) - 3) if len(order) > 3 else '')


def _wall_row(p, target, floor):
    """The plastic round the lead-in and along the insert of one pocket against the wall target.
    None: both walls reach the target. A text: not decided, and why. Else the row inserts.wall reports:
    {"verdict", "measure", "limit", "where", "why", "named", "lead_in", "insert"}."""
    seen = [('at the lead-in', _wall(p, 'lead_in')), ('along the insert', _wall(p, 'insert'))]
    if any(w is None for _, w in seen):
        return 'the wall at the lead-in or along the insert was not measured'
    if _ray_short(p, target, [w for _, w in seen]):
        return 'the walls were measured only out to %s mm, the target is %s' % (_g(p['ray']['max']), _g(target))
    thin = [(where, w) for where, w in seen if w['min'] < target - _EPS]
    if not thin:
        return None
    where, worst = min(thin, key=lambda t: t[1]['min'])
    stops = [_stops(w, target - w['min']) for _, w in thin]
    named = sorted({n for s in stops for n in s})
    if worst['min'] < floor - _EPS:
        verdict, why = 'FAIL', 'under the floor %s' % _g(floor)
    elif all(stops):
        verdict, why = 'BLOCKED', 'under %s, stopped by %s' % (_g(target), _stop_text([w for _, w in thin], named))
        free = max([w['free_arc_deg'] for _, w in thin if _num(w.get('free_arc_deg'))] or [0.0])
        if free > _EPS:
            why += ' (the outline is free over %s deg of the thin arc)' % _g(free)
    else:
        verdict, why = 'GROW', 'under %s, free space over %s deg' % (_g(target), _g(worst.get('free_arc_deg')))
    return {'verdict': verdict, 'measure': worst['min'], 'limit': target, 'where': where, 'why': why, 'named': named,
            'lead_in': seen[0][1]['min'], 'insert': seen[1][1]['min']}


def _relief_row(p, limit):
    """The plastic beside the melt relief of one pocket against the relief wall minimum.
    None: no relief, or its wall reaches the minimum. A text: not decided, and why. Else the row
    inserts.wall_at_relief reports: {"verdict", "measure", "limit", "why", "named", "relief_d", "smaller"}."""
    relief = p.get('relief')
    if 'relief' not in p:
        return 'it was not measured whether it has a melt relief'
    if not isinstance(relief, dict):
        return None
    w = _wall(p, 'relief')
    if w is None:
        return 'it has a melt relief and the wall beside it was not measured'
    if _ray_short(p, limit, [w]):
        return 'the wall beside the relief was measured only out to %s mm' % _g(p['ray']['max'])
    if w['min'] >= limit - _EPS:
        return None
    smaller = sorted([(r['d'], r['wall']) for r in _list(p.get('relief_room')) if isinstance(r, dict) and _num(r.get('d'))
                      and _num(r.get('wall')) and r['wall'] >= limit - _EPS and (not _num(relief.get('d')) or r['d'] < relief['d'] - _EPS)], reverse=True)
    named = _stops(w, limit - w['min'])
    if smaller:
        verdict, why = 'FAIL', 'a D%s relief leaves %s' % (_g(smaller[0][0]), _g(smaller[0][1]))
    elif named:
        verdict, why = 'BLOCKED', 'no smaller relief of the table fits and %s stops the wall' % _stop_text([w], named)
    else:
        verdict, why = 'FAIL', 'free space over %s deg' % _g(w.get('free_arc_deg'))
    return {'verdict': verdict, 'measure': w['min'], 'limit': limit, 'why': why, 'named': named, 'relief_d': relief.get('d'),
            'smaller': [d for d, _ in smaller]}


def _no_relief_fits(p, wall_min):
    """For a pocket WITHOUT a melt relief where no relief diameter of the table would leave wall_min beside it: the
    best one as (d, wall), or () when the facts hold no usable relief_room row. None for every other pocket (it has a
    relief, a relief would fit, or the facts do not say). Such a pocket is BLOCKED in inserts.relief_missing; when its
    walls are thin too, the row of inserts.wall says it and inserts.relief_missing does not repeat it."""
    room = p.get('relief_room')
    if isinstance(p.get('relief'), dict) or 'relief' not in p or not isinstance(room, list):
        return None
    rows = [(r['d'], r['wall']) for r in room if isinstance(r, dict) and _num(r.get('d')) and _num(r.get('wall'))]
    if any(w >= wall_min - _EPS for _, w in rows):
        return None
    return max(rows, key=lambda r: r[1]) if rows else ()


def _relief_own_cause(p, wall, relief, ctx):
    """Is the wall beside the melt relief thin for a reason of its own? The relief is wider than the lead-in, so the
    same outline that leaves W round the lead-in leaves W less half the step between the two diameters beside the
    relief. A relief wall thinner than that (by more than two ray steps of the probe) ends at something else: the
    relief of a neighbour pocket, a groove at the mouth level. Then the number beside the relief is the worst one of
    the pocket and no wider lead-in wall explains it. False when the diameters cannot be read."""
    kinds = _table(ctx, 'inserts').get('kinds') or {}
    row = kinds.get(p.get('kind')) if isinstance(p.get('kind'), str) else None
    lead_d = (row.get('lead_in') or {}).get('d') if isinstance(row, dict) else None
    if not (_num(lead_d) and _num(relief.get('relief_d')) and _num(wall.get('lead_in')) and _num(relief.get('measure'))) or relief['relief_d'] <= lead_d:
        return False
    step = (p.get('ray') or {}).get('step') if isinstance(p.get('ray'), dict) else None
    slack = 2.0 * step if _num(step) and step > 0 else 0.2
    return relief['measure'] < wall['lead_in'] - (relief['relief_d'] - lead_d) / 2.0 - slack - _EPS


def _relief_carries(wall, relief):
    """Which rule has the ONE row of a pocket whose walls are thin for one cause, given the row of each (None = that
    level passes)? True: inserts.wall_at_relief. False: inserts.wall.
    The relief rule has it when the walls round the lead-in pass, and when a smaller relief of the table would fit
    (a fix of its own, at the relief) while the wall is not under its floor. In every other case the wall round the
    lead-in is the headline: a wider wall is the fix for both levels, and a wall under the floor comes first.
    One exception to "one row" is made by inserts.wall_at_relief itself: a relief wall that is thin for a reason of its
    own (_relief_own_cause) is a second finding with a cause and a number of its own, and gets its own row."""
    if relief is None:
        return False
    return wall is None or (bool(relief['smaller']) and wall['verdict'] != 'FAIL')


@judge
def inserts_wall(facts, params, ctx):
    """Rule inserts.wall. Reads fusion_inserts facts: "pockets"[].walls.lead_in / .insert ({"min", "dir", "free_arc_deg",
    "blocked_by"}), .anchor, .kind, .ray. Parameters inserts.wall_target, inserts.wall_floor, inserts.relief_wall_min and
    the "d" and "depth" parameters of every kind of the pockets table.
    Both walls >= target: nothing. The smaller one under the floor: FAIL, whatever stands beside it.
    Under the target and some thin wall has nothing that stops it: GROW (the fix order starts with a wider wall).
    Under the target and every thin wall ends at a neighbour standing closer than the wall has to grow: BLOCKED,
    by = those neighbours (the part's own cavity counts as one).
    measure = the smaller of the two walls, limit = inserts.wall_target, context = kind + both walls + what blocks.
    A wall that was not measured, or rays that ended under the target without leaving the body: NOT_RUN.

    ONE row per pocket for its walls. The wall beside the melt relief is a third level of the same pocket
    (inserts.wall_at_relief). When both rules have a row for one pocket, one of them reports it (_relief_carries: this
    rule, unless a smaller relief of the table would fit and the wall is not under its floor) and its text names the
    other level too. The context holds both, so a waiver ends when either level changes. (A relief wall that is thin
    for a reason of its own is a row of inserts.wall_at_relief besides the row here: two causes, two rows.)
    Not judged here (_not_a_pocket): a pocket for a bought part at the size of its parameter, and a hole too short to
    hold the insert (inserts.floor has that row).
    With ctx["census"]: a hole that is an insert pocket by today's tables and has no measured pocket in these facts
    (the tables changed since the facts were collected, or the far end of a hole with a pocket at each end was not
    measured yet) is NOT_RUN: collect the facts of the part again."""
    target, floor, relief_min = pv(params, 'inserts.wall_target'), pv(params, 'inserts.wall_floor'), pv(params, 'inserts.relief_wall_min')
    pockets, out = _rows(facts, 'pockets', ctx)
    for p in pockets:
        a = _anchor(p.get('anchor'))
        if _not_a_pocket(p, params, ctx):
            continue
        row = _wall_row(p, target, floor)
        if isinstance(row, str):
            out.append(_not_run(ctx, '%s: %s' % (_pocket(p, a), row), a))
        if not isinstance(row, dict):
            continue
        other = _relief_row(p, relief_min)
        other = other if isinstance(other, dict) else None
        if _relief_carries(row, other):
            continue                                     # a smaller relief fits: inserts.wall_at_relief has the row of this pocket
        text = '%s: wall %s %s, %s' % (_pocket(p, a), _g(row['measure']), row['where'], row['why'])
        if other:
            text += '; also %s beside its D%s melt relief, under %s (%s; inserts.wall_at_relief %s)' % (
                _g(other['measure']), _g(other['relief_d']), _g(relief_min),
                'a smaller relief of the table fits' if other['smaller'] else 'free space' if not other['named'] else 'no smaller relief of the table fits', other['verdict'])
        none = _no_relief_fits(p, relief_min)
        if none is not None:
            text += '; no melt relief, and no relief of the table leaves %s beside it%s: an owner decision for the group, with the bench line "shave the melt ring"' % (
                _g(relief_min), ' (the best, D%s, leaves %s)' % (_g(none[0]), _g(none[1])) if none else '')
        context = {'kind': p.get('kind'), 'lead_in': row['lead_in'], 'insert': row['insert'], 'blocked_by': row['named'], 'verdict': row['verdict']}
        if other:
            context['relief'] = [other['measure'], other['verdict']]
        if none is not None:
            context['no_relief'] = list(none)
        out.append(finding(ctx['rule'], ctx['part'], row['verdict'], text, anchor=a, measure=row['measure'], limit=target,
                           by=[] if row['verdict'] == 'GROW' else row['named'], context=context))
    census = ctx.get('census')
    if isinstance(census, dict) and isinstance(census.get('holes'), list):
        have = {}
        for p in pockets:
            if isinstance(p.get('hole'), str):
                have.setdefault(p['hole'], set()).add('exit' if p.get('end') == 'exit' else 'mouth')
        for h in census['holes']:
            c = _hole_class(h, ctx) if isinstance(h, dict) and isinstance(h.get('id'), str) else None
            if not c or c.get('role') != 'insert_pocket' or _bought_pocket(h, params, ctx):
                continue
            a = _anchor(dict(h['anchor'], kind='insert_pocket')) if isinstance(h.get('anchor'), dict) else None
            if 'mouth' not in have.get(h.get('id'), ()):
                out.append(_not_run(ctx, 'hole %s %s at %s is an insert pocket (%s) by the tables of today and the insert probe did not measure it: collect the facts of this part again'
                                    % (h.get('id') or '', _shape(_segments(h)), _at(a), c.get('kind')), a))
            elif c.get('ends') == 2 and 'exit' not in have.get(h.get('id'), ()):
                far = (_anchor(dict(h['exit'], kind='insert_pocket')) if isinstance(h.get('exit'), dict) else None) or a
                out.append(_not_run(ctx, 'hole %s %s has a pocket (%s) at each end and only the one at %s was measured: the pocket at %s was not. Collect the facts of this part again'
                                    % (h.get('id') or '', _shape(_segments(h)), c.get('kind'), _at(a), _at(far)), far))
    return out


@judge
def inserts_wall_at_relief(facts, params, ctx):
    """Rule inserts.wall_at_relief. Reads fusion_inserts facts: "pockets"[].relief, .relief_room, .walls.relief.
    Parameters inserts.relief_wall_min, inserts.wall_target, inserts.wall_floor and the "d" and "depth" parameters of
    every kind of the pockets table.
    inserts.relief_wall_min is a minimum (not a target): a pocket with a relief whose walls.relief.min is under
    it never passes. FAIL when a smaller relief of the table would leave the minimum (relief_room), or when nothing
    stops the wall; BLOCKED, by = the neighbours, when no smaller relief fits and a neighbour stops the wall.
    A relief whose wall was not measured, or a pocket whose facts do not say whether it has a relief: NOT_RUN.
    A pocket without a relief ("relief": null) gives nothing here (inserts_relief_missing owns it).

    ONE row per pocket for its walls (_relief_carries): the row is here when the walls round the lead-in pass, and
    when a smaller relief of the table would fit while the wall is not under its floor; its text then names the wall
    round the lead-in too. In every other case inserts.wall has the row and names the relief wall in its text.
    The exception: a relief wall that is thin for a reason of its own (_relief_own_cause: thinner than the lead-in wall
    and the step between the two diameters explain, as 0.2 mm towards the relief of the next pocket beside a 3.3 wall)
    is a row here ALSO when inserts.wall has its row. It is another cause with another fix, and its number must be the
    number of a row: a text inside the wall row would hide the worst wall of the pocket.
    Not judged here (_not_a_pocket): a pocket for a bought part, and a hole too short to hold the insert."""
    limit, target, floor = pv(params, 'inserts.relief_wall_min'), pv(params, 'inserts.wall_target'), pv(params, 'inserts.wall_floor')
    pockets, out = _rows(facts, 'pockets', ctx)
    for p in pockets:
        a = _anchor(p.get('anchor'))
        if _not_a_pocket(p, params, ctx):
            continue
        row = _relief_row(p, limit)
        if isinstance(row, str):
            out.append(_not_run(ctx, '%s: %s' % (_pocket(p, a), row), a))
        if not isinstance(row, dict):
            continue
        other = _wall_row(p, target, floor)
        other = other if isinstance(other, dict) else None
        if not _relief_carries(other, row) and not (other is not None and _relief_own_cause(p, other, row, ctx)):
            continue                                     # one cause: inserts.wall has the row of this pocket and names this level
        text = '%s: %s of plastic beside its D%s melt relief, under %s; %s' % (_pocket(p, a), _g(row['measure']), _g(row['relief_d']), _g(limit), row['why'])
        if other:
            text += '; also wall %s %s, %s (inserts.wall %s)' % (_g(other['measure']), other['where'], other['why'], other['verdict'])
        context = {'kind': p.get('kind'), 'relief_d': row['relief_d'], 'wall': row['measure'], 'blocked_by': row['named'],
                   'smaller': row['smaller'], 'verdict': row['verdict']}
        if other:
            context['walls'] = [other['lead_in'], other['insert'], other['verdict']]
        out.append(finding(ctx['rule'], ctx['part'], row['verdict'], text, anchor=a, measure=row['measure'], limit=limit, by=row['named'], context=context))
    return out


@judge
def inserts_floor(facts, params, ctx):
    """Rule inserts.floor. Reads fusion_inserts facts: "pockets"[].floor (mm of plastic under a blind pocket, None =
    through), .segments, .relief, .kind. Parameter inserts.floor_min.
    floor under the limit: FAIL with measure = floor.
    The insert longer than its pocket (the depth below the relief, and below a recess in front of it ("recess"), is
    under "insert_len" of the kind's table row; for a
    through pocket that depth is the material): FAIL with measure = that depth, limit = the insert length.
    This is the ONE row of a hole that is too short for its insert: inserts.pocket_shape, inserts.wall,
    inserts.wall_at_relief and inserts.relief_missing say nothing about it (_too_short, _not_a_pocket).
    When the census says the wall of that hole is broken open ("arc" under 1 in ctx["census"]) the row is NOT_RUN,
    not FAIL: a short piece of a cylinder with an open wall is what the census reads of a hole it could not follow as
    one stack (a radial pocket that ends in a curved bore, a hole cut by a slit). Nothing of such a pocket was measured.
    A floor that is neither a number nor None, or a kind with no table row: NOT_RUN.
    A pocket for a bought part at the size of its parameter (_bought_pocket through ctx["census"]) is not judged.
    Parameters: inserts.floor_min and the "d" and "depth" parameters of every kind of the pockets table."""
    limit = pv(params, 'inserts.floor_min')
    kinds = _table(ctx, 'inserts').get('kinds') or {}
    pockets, out = _rows(facts, 'pockets', ctx)
    for p in pockets:
        a, floor, kind = _anchor(p.get('anchor')), p.get('floor'), p.get('kind')
        if _not_a_pocket(p, params, ctx) == 'bought':
            continue
        if 'floor' not in p or not (floor is None or _num(floor)):
            out.append(_not_run(ctx, '%s: the plastic under it was not measured' % _pocket(p, a), a))
        elif floor is not None and floor < limit - _EPS:
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: %s mm of plastic under its floor, under %s' % (_pocket(p, a), _g(floor), _g(limit)),
                               anchor=a, measure=floor, limit=limit, context={'kind': kind, 'floor': floor}))
        segs, row = _segments(p), kinds.get(kind) if isinstance(kind, str) else None
        if not isinstance(row, dict) or not _num(row.get('insert_len')):
            out.append(_not_run(ctx, '%s: the inserts table has no row with a length for this kind' % _pocket(p, a), a))
            continue
        if segs is None:
            continue
        depth = round(sum(s['len'] for s in segs) - sum(x['depth'] for x in (p.get('relief'), p.get('recess'))
                                                        if isinstance(x, dict) and _num(x.get('depth'))), 4)
        if depth < row['insert_len'] - _EPS:
            what = 'the material it goes through' if floor is None else 'its pocket'
            hole = _census_hole(p, ctx)
            arc = hole.get('arc') if hole else None
            if _num(arc) and arc < 1.0 - _EPS:
                out.append(_not_run(ctx, '%s: the census read only %s of this hole, with its wall over %s of the circle, and the insert is %s long: a piece of a hole that was not followed as one stack. Nothing of this pocket was measured: look at the hole in the model and measure its walls by hand'
                                    % (_pocket(p, a), _shape(segs), _pct(arc), _g(row['insert_len'])), a))
                continue
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: the insert is %s long and %s is %s deep: it stands proud' % (_pocket(p, a), _g(row['insert_len']), what, _g(depth)),
                               anchor=a, measure=depth, limit=row['insert_len'], context={'kind': kind, 'depth': depth, 'through': floor is None}))
    return out


@judge
def inserts_relief_missing(facts, params, ctx):
    """Rule inserts.relief_missing. Reads fusion_inserts facts: "pockets"[].relief ({"d", "depth"} or None) and
    .relief_room ([{"d", "wall"}]: the wall that would stay beside a relief of each table diameter).
    Parameters inserts.relief_depth_min, inserts.relief_depth_max, inserts.relief_wall_min.
    A relief shallower than the minimum or deeper than the maximum: FAIL, measure = its depth. A pocket at the bottom
    of a recess ("recess": a wider first step that is no relief diameter of the table: a spot face, a counterbore) is
    judged like any other: the recess is not its relief, the text says so. No relief where some table diameter leaves the
    wall: FAIL, measure = 0. No relief and no table diameter fits: BLOCKED, text names the bench line "shave the melt
    ring", by = what stands beside the lead-in. No relief_room data for a pocket without a relief: NOT_RUN.
    Not judged here (_not_a_pocket): a pocket for a bought part at the size of its parameter, and a hole too short to
    hold the insert (inserts.floor has that row). A pocket with no relief where none fits is not a row here when
    inserts.wall has a row for the same pocket: the thin wall is why nothing fits, and that row says so (ONE row per
    pocket). Parameters besides the three above: inserts.wall_target, inserts.wall_floor and the "d" and "depth"
    parameters of every kind of the pockets table."""
    depth_min, depth_max, wall_min = pv(params, 'inserts.relief_depth_min'), pv(params, 'inserts.relief_depth_max'), pv(params, 'inserts.relief_wall_min')
    pockets, out = _rows(facts, 'pockets', ctx)
    for p in pockets:
        a, relief = _anchor(p.get('anchor')), p.get('relief')
        if _not_a_pocket(p, params, ctx):
            continue
        recess = p.get('recess') if isinstance(p.get('recess'), dict) else None
        where = ' (at the bottom of a D%s x %s recess, which is not a relief)' % (_g(recess.get('d')), _g(recess.get('depth'))) if recess else ''
        if isinstance(relief, dict):
            depth = relief.get('depth')
            if not _num(depth):
                out.append(_not_run(ctx, '%s: the depth of its melt relief was not measured' % _pocket(p, a), a))
            elif depth < depth_min - _EPS:
                out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: the step at its mouth is %s deep, a melt relief is at least %s' % (_pocket(p, a), _g(depth), _g(depth_min)),
                                   anchor=a, measure=depth, limit=depth_min, context={'kind': p.get('kind'), 'relief': depth}))
            elif depth > depth_max + _EPS:
                out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                                   '%s: the D%s step at its mouth is %s deep, a melt relief is at most %s: every deeper millimetre is taken off the thread engagement' % (_pocket(p, a), _g(relief.get('d')), _g(depth), _g(depth_max)),
                                   anchor=a, measure=depth, limit=depth_max, context={'kind': p.get('kind'), 'relief': depth, 'deep': True}))
            continue
        room = p.get('relief_room')
        if 'relief' not in p or not isinstance(room, list):
            out.append(_not_run(ctx, '%s: it was not measured whether it has a melt relief and which one fits' % _pocket(p, a), a))
            continue
        rows = sorted([(r['d'], r['wall']) for r in room if isinstance(r, dict) and _num(r.get('d')) and _num(r.get('wall'))], reverse=True)
        fits = [(d, w) for d, w in rows if w >= wall_min - _EPS]
        if fits:
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s%s: no melt relief; a D%s relief fits and leaves %s beside it' % (_pocket(p, a), where, _g(fits[0][0]), _g(fits[0][1])),
                               anchor=a, measure=0.0, limit=depth_min, context={'kind': p.get('kind'), 'relief': 0.0, 'fits': [d for d, _ in fits]}))
            continue
        if isinstance(_wall_row(p, pv(params, 'inserts.wall_target'), pv(params, 'inserts.wall_floor')), dict):
            continue                                 # the walls are thin: the row of inserts.wall names the missing relief too
        best = max(rows, key=lambda r: r[1]) if rows else None
        w = _wall(p, 'lead_in')
        named = _names(w.get('blocked_by')) if w else []
        text = '%s%s: no melt relief and no relief of the table leaves %s beside it' % (_pocket(p, a), where, _g(wall_min))
        if best:
            text += ' (the best, D%s, leaves %s)' % (_g(best[0]), _g(best[1]))
        text += ': one owner decision for the group, with the bench line "shave the melt ring"'
        out.append(finding(ctx['rule'], ctx['part'], 'BLOCKED', text, anchor=a, measure=0.0, limit=depth_min, by=named,
                           context={'kind': p.get('kind'), 'relief': 0.0, 'fits': [], 'room': [[d, w] for d, w in rows]}))
    return out


@judge
def inserts_mouth_reachable(facts, params, ctx):
    """Rule inserts.mouth_reachable. Reads fusion_openings facts: "openings"[] with "of" == "insert_pocket", their
    "feature", "end" and "paths"["iron"] ({"r", "reach", "free", "hits"}); ctx["census"] for the segments of a through
    pocket. Parameters inserts.iron_d, inserts.iron_reach.
    An insert is set on the bench, in the bare part. So only the part itself (a hit of kind "self") stands in the way
    of the iron. A neighbour does not count, whatever it is: it is not there when the insert is melted in.
    Which end is a mouth: the row with "end" "mouth". A through pocket that is stepped at both ends holds two inserts:
    its far end is a mouth too. A straight through pocket takes its insert from either end: it fails only when the
    part closes both.
    A hole that is too short to hold the insert of its kind (_too_short on its census segments) is not judged here: it
    is a hole that stands proud, or a piece of a hole the census did not follow, and inserts.floor has its one row.
    Own material in the path: FAIL, measure = mm from the mouth to it, limit = inserts.iron_reach, by = the part.
    No iron path, a path measured narrower or shorter than the parameters, own material known only by sample points
    (its cut failed), a straight through pocket closed at one end whose other end was not measured, or a pocket of the
    census (by today's tables) that has no opening row in these facts at all: NOT_RUN.
    Parameters besides the two above: the "d" and "depth" parameters of every kind of the pockets table and the name
    patterns of inserts and screws (a pocket for a bought part is no insert pocket: _bought_pocket)."""
    reach, r_min = pv(params, 'inserts.iron_reach'), pv(params, 'inserts.iron_d') / 2.0
    openings, out = _rows(facts, 'openings', ctx)
    census = ctx.get('census') if isinstance(ctx.get('census'), dict) else {}
    holes = {h.get('id'): h for h in _list(census.get('holes')) if isinstance(h, dict)}
    by_id = {}
    for o in openings:
        if o.get('of') == 'insert_pocket':
            by_id.setdefault(str(o.get('feature')), []).append(o)
        elif o.get('of') in _ACC_HOLES and o.get('end') != 'exit' and isinstance(o.get('feature'), str) and o['feature'] in holes \
                and (_hole_class(holes[o['feature']], ctx) or {}).get('role') == 'insert_pocket':
            out.append(_not_run(ctx, '%s is an insert pocket by today\'s tables, and these facts hold no "iron" path for it: collect the facts of this part again'
                                % _acc_label(o), _anchor(o.get('anchor'))))
    table = _table(ctx, 'inserts')
    seen = {o['feature'] for o in openings if o.get('of') in _ACC_HOLES and isinstance(o.get('feature'), str)}
    for hid in sorted(k for k in holes if isinstance(k, str) and k not in seen):
        cls = _hole_class(holes[hid], ctx) or {}
        segs = _segments(holes[hid]) or []
        if cls.get('role') != 'insert_pocket' or _bought_pocket(holes[hid], params, ctx):
            continue
        if segs and _too_short(segs, (table.get('kinds') or {}).get(cls.get('kind')) if isinstance(cls.get('kind'), str) else None, table.get('tol') or {}):
            continue                                 # inserts.floor has the one row of a hole too short for its insert
        a = _anchor(dict(holes[hid]['anchor'], kind='insert_pocket')) if isinstance(holes[hid].get('anchor'), dict) else None
        out.append(_not_run(ctx, 'pocket %s %s at %s: these facts hold no opening row for it, so the path of the iron was not measured: collect the facts of this part again'
                            % (hid, _shape(segs), _at(a)), a))
    for fid in by_id:
        mouths = [o for o in by_id[fid] if o.get('end') != 'exit']
        far = [o for o in by_id[fid] if o.get('end') == 'exit']
        segs = _segments(holes.get(fid) or {}) or []
        cls = _hole_class(holes.get(fid) or {}, ctx) or {}
        kind = cls.get('kind') if cls.get('table') == 'inserts' else None           # a screw size and an insert kind can share a name
        if segs and _too_short(segs, (table.get('kinds') or {}).get(kind) if isinstance(kind, str) else None, table.get('tol') or {}):
            continue                                 # too short to hold its insert: no pocket to reach. inserts.floor has the one row of this hole
        narrow = min([s['d'] for s in segs] or [0.0])
        both = (len(segs) >= 2 and segs[0]['d'] > narrow + 0.05 and segs[-1]['d'] > narrow + 0.05) or (_hole_class(holes.get(fid) or {}, ctx) or {}).get('ends') == 2
        either = len(segs) == 1 and bool(far)
        states = []
        for o in mouths + (far if both or either else []):
            label, path = _acc_label(o, 'pocket mouth'), _acc_path(o, 'iron')
            if path is None:
                states.append((o, 'unknown', '%s: its "iron" path was not measured' % label))
                continue
            s = _acc_sorted(path, reach, 0.0, None)
            short = _acc_short(path, reach, label, r_min)
            if s['self']:
                states.append((o, 'self', s['self']))
            elif any(h.get('kind') == 'self' for h in s['approx']):
                states.append((o, 'unknown', '%s: the part itself was only sampled with points there (its cut failed): not a measurement' % label))
            elif short:
                states.append((o, 'unknown', short))
            else:
                states.append((o, 'free', None))
        if either and any(st == 'free' for _, st, _ in states):
            continue
        closed = [(o, hits) for o, st, hits in states if st == 'self']
        unknown = [(o, text) for o, st, text in states if st == 'unknown']
        if either and closed and unknown:
            o, hits = closed[0]
            out.append(_not_run(ctx, '%s: the part itself stands in the path of the iron %s mm out; this is a straight through pocket, and its other end was not measured'
                                % (_acc_label(o, 'pocket mouth'), _g(_acc_first(hits))), _anchor(o.get('anchor'))))
            continue
        for o, text in unknown:
            out.append(_not_run(ctx, text, _anchor(o.get('anchor'))))
        for o, hits in (closed[:1] if either else closed):
            first, names = _acc_first(hits), _names(hits)
            shares = [h['share'] for h in hits if _num(h.get('share'))]
            text = '%s: the part itself stands in the path of the iron %s mm out' % (_acc_label(o, 'pocket mouth'), _g(first))
            if shares:
                text += ' and covers %s of it' % _pct(max(shares))
            if either:
                text += '; a straight through pocket, closed at both ends'
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text, anchor=_anchor(o.get('anchor')), measure=first, limit=reach, by=names,
                               context={'of': o.get('of'), 'path': 'iron', 'free': first, 'by': names, 'kinds': ['self']}))
    return out


# ---- bearings (probe fusion_bearings) ---------------------------------------------------------------------------------------------

@judge
def bearings_holding(facts, params, ctx):
    """Rule bearings.holding. Reads fusion_bearings facts: "bearings"[].rings.inner / .outer -> "faces"[] ({"side",
    "covered": [{"name", "kind", "part", "share", "points", "gap", "touches_other_ring"}]}), .designation, .anchor.
    Parameters bearings.cover_share_min, bearings.clamp_points_min, bearings.contact_gap_max.
    A ring face nothing holds (no cover within the gap with enough share, and fewer clamp points in all than the
    minimum): FAIL, one finding per ring face, measure = the best share that is there, limit = the share minimum.
    A cover within the gap that touches both rings: FAIL, one finding per cover and side, by = [the cover]. The other
    bearing of a pair (same bore and outer diameter: ring on ring) is no such cover.
    A bearing with no table row, no rings, or a ring face with no data: NOT_RUN with what to do (a name or a missing
    row is no measurement of the part), never skipped.
    Where a ring is judged: for the part that seats it or holds one of its faces (_bearing_mine). The outer ring of a
    bearing that sits in a neighbour's housing is judged with the neighbour.
    A bearing that bought bodies hold and at whose rings no printed part stands, seats or clamps (_bearing_unheld,
    proven by the facts: the rollers inside a bought gearbox) is no finding of a printed part that is screwed to that
    assembly: ONE INFO row for the part counts these bearings and names their kinds and the mesh bodies near them
    ("mesh_near": a part drawn as a mesh cannot be measured and may hold a ring), nothing else is judged on them here.
    Where the facts cannot prove it, for a bearing that nothing touches at all, and for a ring face a printed part
    stops short of, the bearing is judged as before.
    A row that rests on a name alone while its measured d, D, B contradict that name's table row is no bearing (a clamp
    screw named after the bearing it clamps): one INFO row names them, nothing else is judged on them. So are the
    bodies the probe passed over with no ring geometry ("rejected"[] without d, D, B: a pin, a screw).
    Never silent about a ring: a row whose measures are the table row's in two of three (_bearing_off_row: a 608 drawn
    7.3 wide), a ring the probe passed over ("rejected"[] with d, D, B: named like a bearing, no row of the table)
    and a body named like a bearing whose shape could not be read ("rejected"[] with "unread") are NOT_RUN, one row
    each: a ring that nobody judged is not a pass.
    anchor kind "bearing"; context = designation + ring + side + what covers it."""
    share_min, points_min, gap_max = pv(params, 'bearings.cover_share_min'), pv(params, 'bearings.clamp_points_min'), pv(params, 'bearings.contact_gap_max')
    table = _table(ctx, 'bearings')
    sizes, aliases = table.get('sizes') or {}, table.get('aliases') or {}
    bearings, out = _rows(facts, 'bearings', ctx)
    if bearings and not sizes:
        out.append(_not_run(ctx, 'the bearings table is not there: no bearing can be told from an unknown body'))
    wrong, loose = [], []
    for b in bearings:
        a, des = _anchor(b.get('anchor')), b.get('designation')
        label = _bearing(b, a)
        if _bearing_named_wrong(b, ctx):
            wrong.append(b)                          # named after a bearing, measured as something else: no bearing
            continue
        if _bearing_unheld(b, gap_max):
            loose.append(b)                          # no printed part seats, touches or clamps it: nobody's finding here
            continue
        mine = _bearing_mine(b, ctx['part'], gap_max)
        if not (mine['inner'] or mine['outer']):
            continue                                 # seated and held by other printed parts: judged with them
        if sizes and (not isinstance(des, str) or (des not in sizes and des not in aliases)):
            out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                               '%s: d %s, D %s, B %s is no row of the bearings table: add the row (rules/tables/bearings.json) or correct the ref; its seat and shoulders cannot be held against a size' % (label, _g(b.get('d')), _g(b.get('D')), _g(b.get('B'))),
                               anchor=a, context={'designation': des, 'no_row': [b.get('d'), b.get('D'), b.get('B')]}))
        rings = b.get('rings')
        if not isinstance(rings, dict):
            out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                               '%s: it is known by its name only, the body has no ring geometry (%s): what touches its rings was not measured. Model it as a ring d x D x B' % (label, b.get('why') or 'no rings in the facts'),
                               anchor=a, context={'designation': des, 'no_rings': True}))
            continue
        both = {}
        for ring in ('inner', 'outer'):
            if not mine[ring]:
                continue
            r = rings.get(ring)
            faces = {f.get('side'): f for f in _list(r.get('faces') if isinstance(r, dict) else None) if isinstance(f, dict) and isinstance(f.get('side'), str)}
            for side in ('+', '-'):
                f = faces.get(side)
                if f is None or not isinstance(f.get('covered'), list):
                    out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN', '%s: no holding data for the %s ring face %s' % (label, ring, side),
                                       anchor=a, context={'designation': des, 'ring': ring, 'side': side, 'no_data': True}))
                    continue
                covers = [c for c in f['covered'] if isinstance(c, dict)]
                near = [c for c in covers if _num(c.get('gap')) and c['gap'] <= gap_max + _EPS]
                for c in near:
                    if c.get('touches_other_ring') and not _bearing_twin(c, b, bearings):
                        both[(side, _names([c])[0])] = c
                share = max([c['share'] for c in near if _num(c.get('share'))] or [0.0])
                points = sum(c['points'] for c in near if _num(c.get('points')))
                if share >= share_min - _EPS or points >= points_min:
                    continue
                far = sorted([c for c in covers if c not in near and _num(c.get('gap'))], key=lambda c: c['gap'])
                if near:
                    why = 'held only over %s of the circle at %d point(s) by %s; it needs %s or %s clamps' % (_pct(share), points, ', '.join(_names(near)), _pct(share_min), _g(points_min))
                elif far:
                    why = 'touched by nothing: the nearest, %s, stands %s mm off (contact is within %s)' % (_names(far[:1])[0], _g(far[0]['gap']), _g(gap_max))
                else:
                    why = 'touched by nothing'
                out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: the %s ring face %s is %s' % (label, ring, side, why),
                                   anchor=a, measure=share, limit=share_min, unit='share',
                                   context={'designation': des, 'ring': ring, 'side': side,
                                            'covers': sorted([[_names([c])[0], c.get('share'), c.get('points'), c.get('gap')] for c in covers], key=str)}))
        for (side, name) in sorted(both):
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: %s touches both rings on the %s side: the rings cannot turn against each other' % (label, name, side),
                               anchor=a, by=[name], context={'designation': des, 'side': side, 'both_rings': name}))
    for b in [x for x in wrong if _bearing_off_row(x, ctx)]:
        wrong.remove(b)                              # that bearing, drawn off its row: a ring nobody judged, not "something else"
        a = _anchor(b.get('anchor'))
        out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                           '%s: its body measures %s x %s x %s, the table row of %s says otherwise: it was not judged as a bearing, so what holds its rings and its clamp room are not checked. '
                           'Correct the model of the bought part (its STEP file), or the row of the bearings table' % (
                               _bearing(b, a), _g(b.get('d')), _g(b.get('D')), _g(b.get('B')), b.get('designation')),
                           anchor=a, context={'designation': b.get('designation'), 'off_row': [b.get('d'), b.get('D'), b.get('B')]}))
    solid = []
    for r in _list(facts.get('rejected')):           # what the probe passed over: a name pointed at a bearing and the geometry said no
        if not isinstance(r, dict):
            continue
        if r.get('unread') is True:
            out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                               '%s is named like a bearing and its shape could not be read (%s): it was not judged as a bearing, so what holds it is not checked. '
                               'Look at the body in the model; an imported body with broken edges is replaced by its STEP file' % (r.get('name') or 'a body', r.get('why') or 'no reason given'),
                               context={'unread_bearing': str(r.get('name'))}))
        elif all(_num(r.get(k)) for k in ('d', 'D', 'B')):
            out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                               '%s is a ring %s x %s x %s near this part that a bearing name picked and that is not judged as a bearing (%s): what holds it and its clamp room are not checked. '
                               'Correct the model of the bought part (its STEP file) or its name, or add its size to the bearings table' % (
                                   r.get('name') or 'a body', _g(r['d']), _g(r['D']), _g(r['B']), r.get('why') or 'no reason given'),
                               context={'rejected_ring': str(r.get('name')), 'size': [r['d'], r['D'], r['B']]}))
        else:
            solid.append(r)
    if wrong or solid:
        b = wrong[0] if wrong else None
        names = sorted([str(x.get('ref')) for x in wrong] + [str(x.get('name')) for x in solid])
        n = len(names)
        first = ('%s: named %s, measured %s x %s x %s' % (b.get('ref'), b.get('designation'), _g(b.get('d')), _g(b.get('D')), _g(b.get('B'))) if b
                 else '%s: %s' % (solid[0].get('name'), solid[0].get('why') or 'no ring'))
        out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                           '%d bod%s carr%s a bearing designation in the name and measure%s otherwise (%s): not judged as bearings' % (
                               n, 'y' if n == 1 else 'ies', 'ies' if n == 1 else 'y', 's' if n == 1 else '', first),
                           context={'named_wrong': names}))
    if loose:
        base = lambda n: re.sub(r'\s*(?:#\d+|\(\d+\)|:\d+)\s*$', '', str(n))
        kinds = {}
        for b in loose:
            kinds.setdefault((base(b.get('ref')), b['designation'] if isinstance(b.get('designation'), str) else None), []).append(b)
        order = sorted(kinds, key=lambda k: (-len(kinds[k]), str(k)))
        some = ['%d x %s%s' % (len(kinds[k]), k[0] or 'unnamed', ' (%s)' % k[1] if k[1] else '') for k in order[:3]]
        n = len(loose)
        meshes = sorted({str(m) for b in loose for m in _list(b.get('mesh_near')) if isinstance(m, str)})
        mesh = ('. Near %s lie%s %d bod%s known only as a mesh, which the unit cannot measure: %s%s. A printed part that is drawn as a mesh may hold a ring and is not seen here'
                % ('it' if n == 1 else 'them', 's' if len(meshes) == 1 else '', len(meshes), 'y' if len(meshes) == 1 else 'ies', ', '.join(meshes[:3]),
                   ' and %d more' % (len(meshes) - 3) if len(meshes) > 3 else '')) if meshes else ''
        out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                           '%d bearing%s near this part %s held by bought bodies and by no printed part, and %s not judged for it: %s%s. Nothing printed seats a ring of %s, '
                           'stands at a ring face or clamps it. A ring that a printed part holds or stands at is judged for that part; a bearing that is meant to sit in this part and has '
                           'nothing printed at its rings is drawn in the wrong place%s' % (
                               n, '' if n == 1 else 's', 'is' if n == 1 else 'are', 'was' if n == 1 else 'were', ', '.join(some),
                               ' and %d more kind%s' % (len(order) - 3, '' if len(order) == 4 else 's') if len(order) > 3 else '', 'it' if n == 1 else 'them', mesh),
                           context={'no_printed_holder': sorted(str(b.get('ref')) for b in loose), 'mesh_near': meshes}))
    return out


@judge
def bearings_clamp_room(facts, params, ctx):
    """Rule bearings.clamp_room. Reads fusion_bearings facts: "bearings"[].clamps[] ({"ring", "side", "by", "name", "room",
    "ring_width", "against", "lands_on", "travel", and "gap" / "stop_gap" or the covers of the ring faces}).
    Parameters bearings.clamp_room, bearings.contact_gap_max. room > ring_width - clamp_room: FAIL, measure =
    ring_width - room (the clamp room that is there: how far the clamp can still close past the ring face before it
    lands; negative when the room is longer than the ring), limit = bearings.clamp_room, by = [the clamp].
    A clamp is a ring that is touched on both faces by two bodies. A body that only stands near a face (farther than
    the contact gap) clamps nothing: that row is left to bearings_holding, which fails the face as "touched by nothing".
    A ring is judged for the part that seats it or holds one of its faces (_bearing_mine), like bearings_holding; a
    bearing no printed part holds (_bearing_unheld) is not judged here, bearings_holding counts it in one INFO row.
    A bearing with no clamps list, or a clamp without both numbers: NOT_RUN. An empty list is a bearing no clamp closes
    on: nothing here (bearings_holding says whether it is held)."""
    need, touch = pv(params, 'bearings.clamp_room'), pv(params, 'bearings.contact_gap_max')
    bearings, out = _rows(facts, 'bearings', ctx)
    for b in bearings:
        a = _anchor(b.get('anchor'))
        if _bearing_named_wrong(b, ctx):
            continue
        mine = _bearing_mine(b, ctx['part'], touch)
        if not (mine['inner'] or mine['outer']):
            continue
        if not isinstance(b.get('clamps'), list):
            out.append(_not_run(ctx, '%s: its clamps were not measured' % _bearing(b, a), a))
            continue
        for c in b['clamps']:
            room, width = (c.get('room'), c.get('ring_width')) if isinstance(c, dict) else (None, None)
            if isinstance(c, dict) and isinstance(c.get('ring'), str) and not mine.get(c['ring'], True):
                continue
            if not (_num(room) and _num(width)):
                out.append(_not_run(ctx, '%s: a clamp has no axial room or ring width' % _bearing(b, a), a))
                continue
            if any(g is not None and g > touch + _EPS for g in _clamp_gaps(b, c)):
                continue                             # it stands off the ring: no clamp. bearings.holding has the open face
            have = round(width - room, 4)
            if have >= need - _EPS:
                continue
            name = _names([c])[0]
            lands = (' before it lands on %s' % c['lands_on']) if isinstance(c.get('lands_on'), str) and c['lands_on'] else ''
            if have > _EPS:
                how = '%s can close only %s mm past the face of the %s ring (%s mm wide, held against %s)%s' % (name, _g(have), c.get('ring'), _g(width), c.get('against') or 'its stop', lands)
            else:
                how = 'the room for the %s ring between %s and %s is %s mm, the ring is %s mm wide: %s cannot close on the ring%s' % (
                    c.get('ring'), name, c.get('against') or 'its stop', _g(room), _g(width), name, lands.replace(' before it lands on', ', it lands on'))
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: %s: clamp room %s, it needs %s' % (_bearing(b, a), how, _g(have), _g(need)),
                               anchor=a, measure=have, limit=need, by=[name],
                               context={'designation': b.get('designation'), 'ring': c.get('ring'), 'side': c.get('side'), 'clamp_room': have, 'by': name}))
    return out


@judge
def bearings_seat(facts, params, ctx):
    """Rule bearings.seat. Reads fusion_bearings facts: "bearings"[].seat.inner / .outer ({"d", "part", "len", "entry_d"} or
    None), .d, .D, and "seats_without_bearing"[] ({"hole", "anchor", "d", "designation", "match", "bodies"}).
    Parameter bearings.seat_d_tol.
    A seat whose diameter is off the bearing's by more than the tolerance: FAIL, measure = seat d, limit = bearing d or D.
    A seat whose entry is in the bearing's way by more than the tolerance (an outer seat entered through something
    narrower than D, an inner seat over something wider than d): FAIL, measure = entry_d: the bearing cannot reach it.
    A seat in another printed part is that part's finding: it is not repeated here. A bearing that no printed part
    seats, touches or clamps (_bearing_unheld) has no seat of this part: not judged here (bearings.holding counts it).
    A bearing-sized seat of this part with no recognised bearing in it:
      - bought bodies lie on its axis ("bodies", else the coaxial bodies of the census hole) and the seat has the exact
        table diameter: NOT_RUN. A bearing the name patterns miss looks like this, and "holds no bearing" is not proven;
      - the seat has the exact table diameter and nothing bought on its axis: FAIL "holds no bearing";
      - the hole is only near a bearing diameter (census match "band"): the size alone is a guess, so the hole is
        asked (INFO with ask = "census.unknown_hole"), not failed: a shaft bore looks like this."""
    tol, touch = pv(params, 'bearings.seat_d_tol'), pv(params, 'bearings.contact_gap_max')
    bearings, out = _rows(facts, 'bearings', ctx)
    for b in bearings:
        a, seat = _anchor(b.get('anchor')), b.get('seat')
        if _bearing_named_wrong(b, ctx) or _bearing_unheld(b, touch):
            continue                                 # no bearing; or one no printed part holds (bearings.holding counts it)
        if not isinstance(seat, dict):
            out.append(_not_run(ctx, '%s: its seats were not measured' % _bearing(b, a), a))
            continue
        for ring, key, what in (('inner', 'd', 'the bore of the bearing'), ('outer', 'D', 'the outside of the bearing')):
            s, size = seat.get(ring), b.get(key)
            if s is None:
                continue
            if isinstance(s, dict) and s.get('part') and str(s['part']) != ctx['part']:
                continue                             # the seat of another printed part: judged with that part
            if not (isinstance(s, dict) and _num(s.get('d')) and _num(size)):
                out.append(_not_run(ctx, '%s: its %s seat or its own %s has no number' % (_bearing(b, a), ring, key), a))
                continue
            entry = s.get('entry_d')
            if abs(s['d'] - size) > tol + _EPS:
                out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: its %s seat is D%s, %s is D%s (tolerance %s)' % (_bearing(b, a), ring, _g(s['d']), what, _g(size), _g(tol)),
                                   anchor=a, measure=s['d'], limit=size, by=[str(s['part'])] if s.get('part') else [],
                                   context={'designation': b.get('designation'), 'ring': ring, 'seat_d': s['d'], 'size': size}))
            elif _num(entry) and ((ring == 'outer' and entry < size - tol - _EPS) or (ring == 'inner' and entry > size + tol + _EPS)):
                out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: the way into its %s seat is D%s, %s is D%s: it cannot reach the seat' % (_bearing(b, a), ring, _g(entry), what, _g(size)),
                                   anchor=a, measure=entry, limit=size, by=[str(s['part'])] if s.get('part') else [],
                                   context={'designation': b.get('designation'), 'ring': ring, 'entry_d': entry, 'size': size}))
    empty, more = _rows(facts, 'seats_without_bearing', ctx)
    out += more
    census = ctx.get('census') if isinstance(ctx.get('census'), dict) else {}
    holes = {h.get('id'): h for h in _list(census.get('holes')) if isinstance(h, dict) and isinstance(h.get('id'), str)}
    known = set(str(b.get('ref')) for b in bearings if not _bearing_named_wrong(b, ctx))
    for s in empty:
        a, des = _anchor(s.get('anchor')), s.get('designation') or 'a bearing'
        hole = holes.get(s.get('hole') if isinstance(s.get('hole'), str) else None) or {}
        klass = hole.get('class') if isinstance(hole.get('class'), dict) else {}
        match = s.get('match') or klass.get('match') or 'exact'
        bodies = s.get('bodies') if isinstance(s.get('bodies'), list) else _names([c for c in _list(hole.get('coaxial')) if isinstance(c, dict) and c.get('kind') in ('bought', 'hardware')])
        bodies = sorted(str(n) for n in bodies if str(n) not in known)
        where = 'D%s at %s' % (_g(s.get('d')), _at(a))
        if match != 'exact':
            text = 'hole %s is near the seat diameter of %s and holds no recognised bearing%s: what is this hole?' % (where, des, ('; on its axis: %s' % ', '.join(bodies)) if bodies else '')
            out.append(finding(ctx['rule'], ctx['part'], 'INFO', text, anchor=a, ask='census.unknown_hole',
                               context={'designation': s.get('designation'), 'seat_d': s.get('d'), 'bodies': bodies, 'guess': True}))
        elif bodies:
            out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                               'seat for %s, %s: %s on its axis and no bearing was recognised there. Name the bearing ref so that bearings.ref_pattern or its designation matches, or add its size to the bearing table; "holds no bearing" is not proven' % (
                                   des, where, ('%s lies' if len(bodies) == 1 else '%s lie') % ', '.join(bodies)),
                               anchor=a, context={'designation': s.get('designation'), 'seat_d': s.get('d'), 'bodies': bodies, 'unrecognised': True}))
        else:
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', 'seat for %s holds no bearing: %s' % (des, where),
                               anchor=a, measure=s.get('d') if _num(s.get('d')) else None,
                               context={'designation': s.get('designation'), 'seat_d': s.get('d'), 'empty': True}))
    return out


# ---- walls (probes stl_walls, fusion_thin) ------------------------------------------------------------------------------------------

@judge
def walls_min_material(facts, params, ctx):
    """Rule walls.min_material. Reads stl_walls facts: "regions"[] that are not slits ({"min", "p50", "area", "point",
    "normal", "samples"}), "coverage", "report_under". Parameters walls.min_wall_effective, walls.coverage_min,
    target.line_width.
    A region under one line width, or from two line widths up to the limit: FAIL (the band from one line up to two
    belongs to walls_one_line, so every wall under the limit fails in exactly one of the two judges).
    coverage under the minimum, or report_under below the limit: NOT_RUN "not proven" with the number, beside the
    regions that were found. anchor kind "face" at the region's point and normal; measure = region min; limit = the
    effective minimum."""
    limit, cover_min, line = pv(params, 'walls.min_wall_effective'), pv(params, 'walls.coverage_min'), pv(params, 'target.line_width')
    regions, out = _regions(facts, ctx, slit=False)
    for r in regions:
        t = r['min']
        if t >= limit - _EPS or line - _EPS <= t < 2 * line - _EPS:
            continue
        a, where = _region_text(r)
        why = 'under one line of %s: it will not print' % _g(line) if t < line - _EPS else 'under %s' % _g(limit)
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', 'wall %s mm %s, %s' % (_g(t), where, why), anchor=a, measure=t, limit=limit,
                           context={'kind': 'wall', 'min': t}))
    coverage, shown = facts.get('coverage'), facts.get('report_under')
    if not _num(coverage) or coverage < cover_min - _EPS:
        out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN', 'not proven: the wall probe covered %s of the surface, the rule needs %s' % (_pct(coverage), _pct(cover_min)),
                           measure=coverage if _num(coverage) else None, limit=cover_min, unit='share', context={'not_run': 'coverage', 'coverage': coverage}))
    if not _num(shown) or shown < limit - _EPS:
        out.append(_not_run(ctx, 'not proven: walls were listed only under %s mm, the limit is %s' % (_g(shown), _g(limit))))
    return out


@judge
def walls_slit(facts, params, ctx):
    """Rule walls.slit. Reads stl_walls facts: "regions"[] with "kind" == "slit" ({"min": gap width, "area", "point",
    "normal"}), "report_under". Parameter walls.slit_min. A gap narrower than the limit: FAIL, measure = the gap.
    report_under below the limit: NOT_RUN."""
    limit = pv(params, 'walls.slit_min')
    regions, out = _regions(facts, ctx, slit=True)
    for r in regions:
        if r['min'] >= limit - _EPS:
            continue
        a, where = _region_text(r)
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', 'air gap %s mm %s, under %s: the slicer closes it' % (_g(r['min']), where, _g(limit)),
                           anchor=a, measure=r['min'], limit=limit, context={'kind': 'slit', 'min': r['min']}))
    shown = facts.get('report_under')
    if not _num(shown) or shown < limit - _EPS:
        out.append(_not_run(ctx, 'not proven: gaps were listed only under %s mm, the limit is %s' % (_g(shown), _g(limit))))
    return out


@judge
def walls_one_line(facts, params, ctx):
    """Rule walls.one_line. Reads stl_walls facts: "regions"[] that are not slits, "report_under". Parameter
    target.line_width. A region whose min lies from one line width up to two: FAIL, measure = min, limit = two line
    widths (needs a thin-wall generator and a size from a fit ladder; the owner's waiver names the ladder).
    report_under below two line widths: NOT_RUN."""
    line = pv(params, 'target.line_width')
    regions, out = _regions(facts, ctx, slit=False)
    for r in regions:
        t = r['min']
        if not (line - _EPS <= t < 2 * line - _EPS):
            continue
        a, where = _region_text(r)
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', 'wall %s mm %s: between one and two lines of %s, it prints as one line and off size' % (_g(t), where, _g(line)),
                           anchor=a, measure=t, limit=round(2 * line, 4), context={'kind': 'one_line', 'min': t, 'line': line}))
    shown = facts.get('report_under')
    if not _num(shown) or shown < 2 * line - _EPS:
        out.append(_not_run(ctx, 'not proven: walls were listed only under %s mm, two lines are %s' % (_g(shown), _g(2 * line))))
    return out


def _thin_sides(face):
    """The long sides of a narrow face: the rows [length, turn, neighbour id] of its "sides" that are at least half as
    long as the longest one. None when the probe wrote no sides, a row is not three values, or a long side has no turn:
    then nothing is known about how the face joins its neighbours."""
    rows = face.get('sides')
    if not isinstance(rows, list) or not rows:
        return None
    if not all(isinstance(r, list) and len(r) == 3 and _num(r[0]) for r in rows):
        return None
    longest = max(r[0] for r in rows)
    long = [r for r in rows if r[0] >= 0.5 * longest - _EPS]
    return long if all(_num(r[1]) for r in long) else None


def _thin_is_strip(face, turn_max):
    """Is the face one flat of a chamfer, a bevel or a faceted curve: every long side turns by at most `turn_max`
    degrees, and the long sides that turn by more than half of it all turn the same way (both fall away, or both rise).
    A ledge and a crescent have one side that rises and one that falls, about 90 degrees each; the top of a thin rib has
    two sides that fall by 90: neither is a strip. Unknown sides: not a strip."""
    long = _thin_sides(face)
    if long is None:
        return False
    if max(abs(r[1]) for r in long) > turn_max + _EPS:
        return False
    return len({r[1] > 0 for r in long if abs(r[1]) > turn_max / 2.0 + _EPS}) <= 1


def _thin_kind(face, turn_max):
    """A word for a narrow face that is not a strip, from the way its long sides turn; '' when the sides are not known."""
    long = _thin_sides(face)
    if long is None:
        return ''
    ways = {r[1] > 0 for r in long if abs(r[1]) > turn_max / 2.0 + _EPS}
    if ways == {True, False}:
        return 'a ledge or a step: one long side rises, the other falls away'
    if ways == {True}:
        return 'a land: the long sides fall away, the material under it is as narrow as the face'
    if ways == {False}:
        return 'the floor of a groove: the long sides rise'
    return ''


def _thin_regions(faces):
    """Narrow faces that touch are one place: the lists of faces joined through their "sides" (a face names the id of
    each neighbour). A face without an id stands alone. Order: as the faces come."""
    by_id = {f['id']: i for i, f in enumerate(faces) if _num(f.get('id'))}
    root = list(range(len(faces)))

    def find(i):
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i
    for i, f in enumerate(faces):
        if not _num(f.get('id')):
            continue
        for r in _list(f.get('sides')):
            j = by_id.get(r[2]) if isinstance(r, list) and len(r) == 3 and _num(r[2]) else None
            if j is not None:
                root[find(i)] = find(j)
    groups = {}
    for i, f in enumerate(faces):
        groups.setdefault(find(i), []).append(f)
    return list(groups.values())


def _thin_zones(census, step):
    """Where bridge steps can be: one zone per step DOWN in diameter on the axis of a hole of the census, from a
    counterbore to the narrower hole under it. The zone reaches from two bridge steps above the end of the wide
    cylinder to the start of the narrow one.

    A counterbore that opens on the bed is printed as a ceiling. The usual print aid: two layers that bridge it, the
    first leaves a slot as wide as the hole, the second a square. In the model these are small ledges and low walls
    between the counterbore and its hole, each tangent to the hole. The census sees such a pair as one hole with a
    step in its segments, or as two holes on one axis with the steps between them.
    Returns [{"o": mouth of the wide hole, "a": its axis (out of the material), "lo", "hi": depth range under that
    mouth, "r": radius of the counterbore, "r2": radius of the hole}]."""
    holes = [(_thin_place(h.get('at')) or _thin_place(h.get('anchor')), _segments(h)) for h in _list((census or {}).get('holes')) if isinstance(h, dict)]
    holes = [(place, segs) for place, segs in holes if place and segs]
    zones, tol = [], 0.06
    for (o, a), segs in holes:
        t = 0.0
        for i, s in enumerate(segs):
            t += s['len']
            nxt = segs[i + 1] if i + 1 < len(segs) else None
            if nxt is not None and s['d'] - nxt['d'] > 0.5:
                zones.append({'o': o, 'a': a, 'lo': t - 2 * step - tol, 'hi': t + tol, 'r': s['d'] / 2.0, 'r2': nxt['d'] / 2.0})
        for (ob, ab), other in holes:
            if other is segs or sum(x * y for x, y in zip(a, ab)) < 0.999 or segs[-1]['d'] - other[0]['d'] <= 0.5:
                continue
            w = [p - q for p, q in zip(ob, o)]
            depth = -sum(x * y for x, y in zip(w, a))
            off = max(0.0, sum(x * x for x in w) - depth * depth) ** 0.5
            if off <= 0.1 and -tol <= depth - t <= 2 * step + tol:
                zones.append({'o': o, 'a': a, 'lo': t - 2 * step - tol, 'hi': depth + tol, 'r': segs[-1]['d'] / 2.0, 'r2': other[0]['d'] / 2.0})
    return zones


def _thin_place(a):
    """(point, unit axis) of an anchor-like object of the facts as the probe wrote them, not rounded to the anchor
    grid: the bridge steps are told by 0.06 mm. None when it is not a point and a direction."""
    if not isinstance(a, dict) or not _xyz(a.get('point')) or not _xyz(a.get('axis')):
        return None
    n = sum(c * c for c in a['axis']) ** 0.5
    if n < 1e-9:
        return None
    return [float(c) for c in a['point']], [c / n for c in a['axis']]


def _thin_bridge(region, zones):
    """Are these touching faces the bridge steps of one counterbore: every face lies in one zone (_thin_zones) and is
    either a tread (it looks out of the mouth) or a riser (square to the axis, facing it, its plane tangent to the
    narrow hole), with at least one of each. A face that looks away from the mouth, or a low wall that is not tangent
    to the hole, is not part of the print aid: the place is judged as a leftover."""
    tol = 0.06
    for z in zones:
        treads = risers = 0
        for f in region:
            place = _thin_place(f.get('anchor'))
            if place is None:
                break
            w = [p - q for p, q in zip(place[0], z['o'])]
            along = sum(x * y for x, y in zip(w, z['a']))
            off = max(0.0, sum(x * x for x in w) - along * along) ** 0.5
            if not (z['lo'] <= -along <= z['hi'] and off <= z['r'] + tol):
                break
            look = sum(x * y for x, y in zip(place[1], z['a']))
            if look >= 0.99:
                treads += 1
            elif abs(look) <= 0.05 and abs(-sum(x * y for x, y in zip(w, place[1])) - z['r2']) <= tol:
                risers += 1
            else:
                break
        else:
            if treads and risers:
                return True
    return False


def _thin_circle(points, tol):
    """The circle through `points` (at least three, [x, y, z]): (centre, unit normal, radius), or None when they do not
    all lie on one circle within `tol` mm (in its plane and in radius). The circle is laid through three of them that
    lie far apart: the first, the one farthest from it, and the one farthest from the line through those two."""
    sub = lambda a, b: [a[i] - b[i] for i in range(3)]
    dot = lambda a, b: sum(a[i] * b[i] for i in range(3))
    cross = lambda a, b: [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    p0 = points[0]
    p1 = max(points, key=lambda p: dot(sub(p, p0), sub(p, p0)))
    a = sub(p1, p0)
    p2 = max(points, key=lambda p: dot(cross(sub(p, p0), a), cross(sub(p, p0), a)))
    b = sub(p2, p0)
    n = cross(a, b)
    nn = dot(n, n)
    if nn < 1e-9:
        return None                                  # on one line
    t1, t2 = cross(b, n), cross(n, a)
    c = [p0[i] + (dot(a, a) * t1[i] + dot(b, b) * t2[i]) / (2.0 * nn) for i in range(3)]
    n = [x / nn ** 0.5 for x in n]
    r = dot(sub(p0, c), sub(p0, c)) ** 0.5
    for p in points:
        w = sub(p, c)
        h = dot(w, n)
        if abs(h) > tol or abs(max(0.0, dot(w, w) - h * h) ** 0.5 - r) > tol:
            return None
    return c, n, r


def _thin_profiles(places, n_min=12, tol=0.05):
    """Equal places that repeat round one axis: what the teeth of a pulley or a gear, a spline or a knurl look like in
    the facts. The test is geometric and names nothing: twelve equal crescents of old holes on one even bolt circle
    are one row too, and that row is as much a FAIL as the twelve were.

    places  [{"faces": the faces of one place that are judged, "more": how many strips lie with them, "kind": the word
             of _thin_kind for its narrowest face, "point": where the place is ([x, y, z]: the mean of the points of
             all its faces, which is the same spot of every copy of a pattern)}]
    Two places are EQUAL when they hold the same number of judged faces and of strips, their narrowest faces join
    their neighbours the same way ("kind"), and their sorted widths differ pairwise by at most `tol` mm.
    Equal places REPEAT round one axis when there are at least `n_min` of them, all of them lie on one circle within
    `tol` mm (in its plane and in radius), and every one sits on a step of one even division of that circle (one
    pitch = the full turn over a whole number of at least `n_min`, each place within `tol` mm of its step along the
    arc). Teeth with a few missing still do (a set-screw hole through two of them); twelve equal ledges anywhere on a
    part do not, and neither do fewer than `n_min` equal places on a bolt circle: those stay one row each, to be
    looked at one by one. Whenever the test is not met the places stay single rows: it can only join rows, never
    drop one.
    The two numbers are decided, not measured: 12 places is more than a bolt circle usually has and no more than the
    smallest toothed wheel in use; 0.05 mm is the grid every anchor and context of the gate is rounded to.
    Returns (profiles, rest): profiles = [{"places": the places of one profile in the order of the facts, "centre",
    "axis" (unit, the sign made canonical), "radius", "steps": the whole number of the division}], rest = the places
    that are in no profile, in the order of the facts."""
    groups = []
    for p in places:
        widths = sorted(f['width'] for f in p['faces'])
        key = (len(p['faces']), p['more'], p['kind'])
        for g in groups:
            if g['key'] == key and all(abs(x - y) <= tol + _EPS for x, y in zip(widths, g['widths'])):
                g['places'].append(p)
                break
        else:
            groups.append({'key': key, 'widths': widths, 'places': [p]})
    profiles, taken = [], set()
    for g in groups:
        pts = [p['point'] for p in g['places']]
        if len(pts) < n_min or any(not _xyz(q) for q in pts):
            continue
        fit = _thin_circle(pts, tol)
        if fit is None:
            continue
        c, n, r = fit
        if r < 1e-6:
            continue
        w0 = [pts[0][i] - c[i] for i in range(3)]
        u = [x / r for x in w0]
        v = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]]
        angles = sorted(math.atan2(sum((q[i] - c[i]) * v[i] for i in range(3)), sum((q[i] - c[i]) * u[i] for i in range(3))) % (2 * math.pi) for q in pts)
        gaps = [(angles[(i + 1) % len(angles)] - angles[i]) % (2 * math.pi) for i in range(len(angles))]
        gaps[-1] = gaps[-1] or 2 * math.pi
        if min(gaps) * r <= tol:
            continue                                 # two places at one spot: no division of a circle
        near = [x for x in gaps if x < 1.5 * min(gaps)]              # the gaps of one step: their mean is the pitch, free of rounding noise
        steps = int(round(2 * math.pi * len(near) / sum(near)))
        pitch = 2 * math.pi / steps
        if steps < n_min or any(abs(a / pitch - round(a / pitch)) * pitch * r > tol for a in angles):
            continue
        sign = next((1.0 if x > 0 else -1.0 for x in n if abs(x) > 1e-6), 1.0)
        profiles.append({'places': g['places'], 'centre': [round(x, 3) + 0.0 for x in c], 'axis': [round(sign * x, 4) + 0.0 for x in n], 'radius': r, 'steps': steps})
        taken.update(id(p) for p in g['places'])
    return profiles, [p for p in places if id(p) not in taken]


@judge
def walls_thin_faces(facts, params, ctx):
    """Rule walls.thin_faces. Reads fusion_thin facts: "faces"[] ({"anchor", "width", "area", "shape"}, and where the
    probe could read them "id" and "sides": [[length, turn in degrees, id of the neighbour face]]), "width_cap",
    "lumps", "non_manifold_edges", "seam_edges". Parameters walls.min_face_width, walls.strip_turn_max,
    overhang.bridge_step. ctx["census"]: the holes of the part, to know the bridge steps of a counterbore.

    A narrow face is not a finding by its width alone. It is judged by how it joins its neighbours:
      a strip (_thin_is_strip: one flat of a chamfer, a bevel, a faceted curve such as a tooth flank): no finding;
      bridge steps of a counterbore (_thin_bridge): no finding;
      anything else under the limit, and every face whose sides the probe did not read: FAIL, measure = width. That
      is the ledge, the crescent of an old hole, the sliver, the land of a rib.
    Narrow faces that touch are ONE finding (_thin_regions) with the count and the narrowest width: the steps round
    one hole are one row, and one waiver covers the place. What was left out is said in one INFO row each (strips,
    bridge steps), never in silence.
    Equal places that repeat round one axis (_thin_profiles: at least 12 places with the same faces and widths on one
    circle, on the steps of one even division: the teeth of a pulley, a spline, a knurl) are ONE finding for the
    profile: FAIL with the count, the axis, the pitch and one place as the example. Its anchor is the point of the
    axis in the plane of the places (kind "face", the axis as its direction); its context is the count of places,
    the division, the radius and the faces and widths of one place. So a waiver of that row covers the whole profile
    while it stays as it is; it expires when a place joins, leaves or changes; and it can never cover another defect
    of the part: a place that differs is no member of the profile and has a row with an anchor and a context of
    its own.
    lumps above 1, or an edge with more than two faces: FAIL (features meet on an edge only). An edge count from facts
    that do not tell a seam from such an edge: NOT_RUN.
    Facts of an older unit, which read no sides at all (no "seam_edges", no "sides_unread", no face with "id" or
    "sides"): ONE NOT_RUN row for the part with the count of narrow faces. A face that the present unit could not read
    (it is counted in "sides_unread") stays a FAIL: that is one place, and the owner can waive a place.
    width_cap below the limit (faces were listed only up to a smaller width), or a face without a width: NOT_RUN."""
    limit, turn_max, step = pv(params, 'walls.min_face_width'), pv(params, 'walls.strip_turn_max'), pv(params, 'overhang.bridge_step')
    faces, out = _rows(facts, 'faces', ctx)
    under = []
    for f in faces:
        a, w = _anchor(f.get('anchor')), f.get('width')
        if not _num(w):
            out.append(_not_run(ctx, 'a thin face at %s has no width' % _at(a), a))
        elif w < limit - _EPS:
            under.append(f)
    if under and 'seam_edges' not in facts and 'sides_unread' not in facts and not any('sides' in f or 'id' in f for f in under):
        # facts of a unit that never looked at the sides (it wrote none of the keys of the one that does): the width alone
        # is no measurement of a leftover, so the rule cannot decide. One row for the part, not one per face
        f = min(under, key=lambda x: x['width'])
        out.append(_not_run(ctx, '%d faces under %s mm wide (the narrowest %s mm at %s) were listed by a unit that did not read how a face joins its neighbours: the width alone '
                                 'does not tell a ledge or a sliver from a tooth flat or a chamfer. Run the part again: the unit now reads the sides of each narrow face'
                            % (len(under), _g(limit), _g(f['width']), _at(_anchor(f.get('anchor'))))))
        under = []
    zones = _thin_zones(ctx.get('census'), step) if isinstance(ctx.get('census'), dict) else []
    strips = strip_places = steps = step_places = 0
    places = []
    for region in _thin_regions(under):
        odd = [f for f in region if not _thin_is_strip(f, turn_max)]
        if not odd:
            strips, strip_places = strips + len(region), strip_places + 1
            continue
        if len(odd) > 1 and zones and _thin_bridge(odd, zones):
            steps, step_places = steps + len(region), step_places + 1
            continue
        pts = [p[0] for p in (_thin_place(f.get('anchor')) for f in region) if p]
        places.append({'faces': odd, 'more': len(region) - len(odd), 'kind': _thin_kind(min(odd, key=lambda x: x['width']), turn_max),
                       'point': [sum(p[i] for p in pts) / len(pts) for i in range(3)] if len(pts) == len(region) else None})
    profiles, single = _thin_profiles(places)

    def one_place(place, each=''):
        """(narrowest face, its anchor, the text of one place, its context)."""
        odd = place['faces']
        f = min(odd, key=lambda x: x['width'])
        a, w = _anchor(f.get('anchor')), f['width']
        if len(odd) == 1:
            text = 'face %s mm wide at %s (%s, %s mm2), under %s' % (_g(w), _at(a), f.get('shape') or 'shape not given', _g(f.get('area')), _g(limit))
            context = {'shape': f.get('shape'), 'width': w}
        else:
            widths = sorted(x['width'] for x in odd)
            text = '%d narrow faces that touch, %s to %s mm wide, the narrowest at %s, under %s' % (len(odd), _g(widths[0]), _g(widths[-1]), _at(a), _g(limit))
            context = {'faces': len(odd), 'widths': widths}
        if place['more']:
            text += '; %d more faces of %s place are flats of a chamfer or of a faceted curve' % (place['more'], each or 'the')
        if place['kind']:
            text += ' (%s)' % place['kind']
        return f, a, text, context
    for place in single:
        f, a, text, context = one_place(place)
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text, anchor=a, measure=f['width'], limit=limit, context=context))
    for prof in profiles:
        f, a, text, context = one_place(prof['places'][0], 'each')
        n = len(prof['places'])
        cols = list(zip(*[sorted(x['width'] for x in p['faces']) for p in prof['places']]))          # the widths of every place, narrowest first
        mid = [sorted(c)[len(c) // 2] for c in cols]                                                 # their medians: the id does not hang on which place the facts list first
        context = dict(context, **({'width': mid[0]} if 'width' in context else {'widths': mid}))
        missing = ' (%d steps of the division hold no such place)' % (prof['steps'] - n) if prof['steps'] > n else ''
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                           '%d equal places repeat on one circle round one axis (through %s along %s, radius %s mm, one every %s deg%s): one row for all of them, as for the teeth of a wheel, a spline or a knurl. '
                           'One of them: %s. One decision covers them all; a place that differs from these has a row of its own'
                           % (n, _at({'kind': 'face', 'point': prof['centre']}), _at({'kind': 'face', 'point': prof['axis']}), _g(prof['radius']), _g(360.0 / prof['steps']), missing, text),
                           anchor=_face(prof['centre'], prof['axis']), measure=f['width'], limit=limit,
                           context={'profile': n, 'steps': prof['steps'], 'radius': prof['radius'], 'place': context, 'more': prof['places'][0]['more'], 'kind': prof['places'][0]['kind']}))
    if strips:
        out.append(finding(ctx['rule'], ctx['part'], 'INFO', '%d faces under %s mm wide in %d place(s) are flats of a chamfer, a bevel or a faceted curve (their long sides turn by at most %s deg): '
                           'not slivers. How thick the material behind them is, the wall check on the STL measures' % (strips, _g(limit), strip_places, _g(turn_max)),
                           context={'strips': strips, 'places': strip_places}))
    if steps:
        out.append(finding(ctx['rule'], ctx['part'], 'INFO', '%d faces under %s mm wide are bridge steps between a counterbore and its hole, at %d such place(s): a print aid, not slivers'
                           % (steps, _g(limit), step_places), context={'bridge_steps': steps, 'places': step_places}))
    lumps, bad = facts.get('lumps'), facts.get('non_manifold_edges')
    if _num(lumps) and lumps > 1:
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', 'the body is %d separate lumps, not one solid' % lumps, measure=lumps, limit=1, unit='lumps', context={'lumps': lumps}))
    if _num(bad) and bad > 0:
        if 'seam_edges' in facts:
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%d edges join more than two faces: two volumes of the body meet on an edge only' % bad,
                               measure=bad, limit=0, unit='edges', context={'non_manifold_edges': bad}))
        else:
            out.append(_not_run(ctx, '%d edges do not join exactly two faces, and these facts do not tell the seam of a closed face (no defect) from an edge where '
                                     'two volumes meet: run the part again, the unit now counts the two apart' % bad))
    cap = facts.get('width_cap')
    if not _num(cap) or cap < limit - _EPS:
        out.append(_not_run(ctx, 'not proven: faces were listed only up to %s mm wide, the limit is %s' % (_g(cap), _g(limit))))
    return out


# ---- access (probe fusion_openings) -----------------------------------------------------------------------------------------------

@judge
def access_hole_covered(facts, params, ctx):
    """Rule access.hole_covered. Reads fusion_openings facts: "openings"[] ({"feature", "of", "end", "anchor",
    "paths": {"own": {"r", "reach", "hits": [{"name", "kind", "at", "share", "approx", "expected"}]}}}) and
    ctx["census"] (the holes with their segments, for what each hole is). Parameters access.reach,
    access.min_blocked_share, access.seal_gap, access.cap_max, access.overlay_pattern.

    What must be free depends on what the opening is for. Holes on one axis are one bore (_acc_bores); only its outer
    ends are judged.
      bearing seat      the side the bearing goes in from (the wide end of a stepped seat, either end of a straight
                        through seat), for the width of the bearing; only the part itself can stand in the way there.
                        What is mounted in front of a seat later is a matter of the insertion path, not of this rule.
      screw hole        (a hole of the screws table, a pilot of the inserts table) the key side, for access.reach: the
                        one end of a blind hole; the wide end of a counterbore; the end that is not the pocket mouth
                        when the bore holds an insert pocket; of a straight through hole the end that is better off,
                        because the screw can come from either side. A bore with the head of a screw ref on it is
                        left to access.screw_path.
      insert pocket     not here: inserts.mouth_reachable.
      hole of no table  nothing is asked here: census.unknown_hole asks what it is.
      slot, window, any other kind   with a bought part or a fastener in the opening itself (the row's "inside" list,
                        [{"name", "kind", "approx"}]: a lock screw in its slot, a motor in its window), measured: that
                        is what it is for, nothing is asked. A mesh, a body known by sample points, a hidden body and
                        a printed neighbour inside it say nothing about what it is for: they are judged as what stands
                        in its path. Facts without that list (an older probe): a hit of kind "bought" or "hardware" at
                        the very mouth stands for it. Else like a key side.
    What stands in a judged path, not expected, with at least the minimum share:
      the part itself, measured        FAIL per opening, measure = mm from the mouth, limit = the length that must be free
      another solid body, measured     ONE row for the part with ask = "assembly.order" that lists every covering
                                       component nearest first (_acc_grouped): one question, answered once
      a mesh, a failed cut             one NOT_RUN row per body: an approximation is never a FAIL
      a body of access.overlay_pattern not counted; one INFO row says so
    The part itself at the very mouth of a slot or window is the rim of the opening (its path is the circle that fits
    into it), not a cover. No path, a free path followed for less than the length, a hole that is not in the census,
    no census at all, a seat or screw hole of the census with no opening row at an outer end (_acc_no_end): NOT_RUN."""
    reach, share = pv(params, 'access.reach'), pv(params, 'access.min_blocked_share')
    gap, cap = pv(params, 'access.seal_gap'), pv(params, 'access.cap_max')
    openings, out = _rows(facts, 'openings', ctx)
    overlay = _acc_overlay(params, ctx, out)
    bores, loose = _acc_bores(openings, ctx, cap, out)
    groups = {'body': {}, 'approx': {}, 'overlay': {}}
    heads = [a for a in (_anchor(o.get('anchor')) for o in loose if o.get('of') == 'screw_head') if a is not None]
    held, blind = None, []
    if isinstance(facts.get('screws'), list) and not facts.get('screws_cut'):
        held = {i for s in facts['screws'] if isinstance(s, dict) for i in _list(s.get('holes')) if isinstance(i, str)}
    if not isinstance(ctx.get('census'), dict):
        n = len([b for b in bores if any(o.get('of') != 'insert_pocket' for o in b['ends'])])
        if n:
            out.append(_not_run(ctx, 'the hole census of this part is not at hand: what its %d holes are for is not known, so none of them was judged' % n))
        bores = []
    for b in bores:
        use, hole, cls = _acc_use(b, ctx, params)
        ends = list(b['ends'])
        if use is not None and not ends:
            out.append(_acc_no_end(b, ctx))                          # a seat or a screw hole with no row at an outer end: not measured, never "free"
            continue
        if use is None:
            continue
        if use == 'seat':
            sizes = _table(ctx, 'bearings').get('sizes')
            row = sizes.get(cls.get('kind')) if isinstance(sizes, dict) else None
            segs = _segments(hole) or []
            width = row['B'] if isinstance(row, dict) and _num(row.get('B')) else (segs[0]['len'] if segs else None)
            if not _num(width):
                out.append(_not_run(ctx, '%s: the width of its bearing is not known' % _acc_label(ends[0], 'bearing seat'), _anchor(ends[0].get('anchor'))))
                continue
            am = _anchor(hole.get('anchor'))
            if len(ends) > 1 and len({round(s['d'], 1) for s in segs}) > 1 and am is not None:      # stepped: the bearing goes in at the wide end
                ends = [o for o in ends if _anchor(o.get('anchor')) and sum(x * y for x, y in zip(_anchor(o.get('anchor'))['axis'], am['axis'])) > 0] or ends[:1]
            closed, unknown = [], []
            for o in ends:
                label, path = _acc_label(o, 'bearing seat'), _acc_path(o, 'own')
                if path is None:
                    unknown.append((o, '%s: its own path was not measured' % label))
                    continue
                s = _acc_sorted(path, width, share, overlay)
                if s['self']:
                    closed.append((o, s['self']))
                elif any(h.get('kind') == 'self' for h in s['approx']):
                    unknown.append((o, '%s: the part itself was only sampled with points there (its cut failed): not a measurement' % label))
                elif _acc_short(path, width, label):
                    unknown.append((o, _acc_short(path, width, label)))
            if len(closed) + len(unknown) < len(ends):
                continue                                             # one end is free: the bearing goes in there
            for o, text in unknown:
                out.append(_not_run(ctx, text, _anchor(o.get('anchor'))))
            if unknown:
                continue
            o, hits = closed[0]
            first, names = _acc_first(hits), _names(hits)
            shares = [h['share'] for h in hits if _num(h.get('share'))]
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                               '%s (%s): the part itself stands %s mm in front of it%s: a bearing %s mm wide cannot go in%s'
                               % (_acc_label(o, 'bearing seat'), cls.get('kind'), _g(first), ' and covers %s of it' % _pct(max(shares)) if shares else '',
                                  _g(width), ' from either end' if len(closed) > 1 else ''),
                               anchor=_anchor(o.get('anchor')), measure=first, limit=width, by=names,
                               context={'of': o.get('of'), 'path': 'own', 'free': first, 'by': names, 'kinds': ['self']}))
            continue
        if b['axis'] is not None and any(abs(sum(x * y for x, y in zip(h['axis'], b['axis']))) >= 0.999 and _acc_off(h['point'], b['origin'], b['axis']) <= 0.3
                                         and b['lo'] - cap <= _acc_s(h['point'], b['origin'], b['axis']) <= b['hi'] + cap for h in heads):
            continue                                                 # the head of a screw ref sits on this bore: access.screw_path
        if held is not None and any(i in held for i in b['ids']):
            continue                                                 # a screw ref lies in it, with its head on another part: judged where the head sits
        if held is None and any(isinstance(c, dict) and c.get('kind') in ('bought', 'hardware') for h in b['holes'] for c in _list(h.get('coaxial'))):
            blind.append(_acc_label(next((o for o in ends if o.get('end') != 'exit'), ends[0]), 'screw hole'))      # something sits in it; where is its head?
            continue
        if len(ends) == 2:
            pockets = {h.get('id') for h in b['holes'] if (_hole_class(h, ctx) or {}).get('role') == 'insert_pocket'}
            keep = [o for o in ends if not (isinstance(o.get('feature'), str) and o['feature'] in pockets and o.get('end') != 'exit')]
            radii = [(_acc_path(o, 'own') or {}).get('r') for o in ends]
            if len(keep) == 1:
                ends = keep
            elif _num(radii[0]) and _num(radii[1]) and abs(radii[0] - radii[1]) > 0.05:
                ends = [ends[0] if radii[0] > radii[1] else ends[1]]
        best = None
        for o in ends:
            label, path = _acc_label(o, 'screw hole'), _acc_path(o, 'own')
            if path is None:
                cand = (1, o, '%s: its own path was not measured' % label, None)
            else:
                s = _acc_sorted(path, reach, share, overlay)
                short = _acc_short(path, reach, label)
                rank = 4 if s['self'] else 3 if s['body'] else 2 if s['approx'] else 1 if short else 0
                cand = (rank, o, short, s)
            if best is None or cand[0] < best[0]:
                best = cand
        rank, o, text, s = best
        label, a = _acc_label(o, 'screw hole'), _anchor(o.get('anchor'))
        if s is not None:
            _acc_note(groups, 'overlay', s['overlay'], label)
        if rank == 0:
            continue
        if rank == 1:
            out.append(_not_run(ctx, text, a))
        elif rank == 4:
            first, names = _acc_first(s['self']), _names(s['self'])
            shares = [h['share'] for h in s['self'] if _num(h.get('share'))]
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                               '%s: the part itself stands in the path of the key %s mm out%s%s'
                               % (label, _g(first), ' and covers %s of it' % _pct(max(shares)) if shares else '', '; its other end is not free either' if len(ends) > 1 else ''),
                               anchor=a, measure=first, limit=reach, by=names,
                               context={'of': o.get('of'), 'path': 'own', 'free': first, 'by': names, 'kinds': ['self']}))
        else:
            _acc_note(groups, 'body', s['body'], label)
            _acc_note(groups, 'approx', s['approx'], label)
    if blind:
        out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                           '%d screw holes hold a bought body on their axis, and these facts do not say whether it is a screw and where its head sits (%s). '
                           'They come from a probe that knew screws by their name only, or its look at the shapes was cut short: collect the facts of this part again'
                           % (len(blind), '; '.join(blind[:6]) + ('; ...' if len(blind) > 6 else '')), context={'no_screw_facts': sorted(blind)}))
    for o in loose:
        if o.get('of') == 'screw_head':
            continue
        a, label, path = _anchor(o.get('anchor')), _acc_label(o), _acc_path(o, 'own')
        if path is None:
            out.append(_not_run(ctx, '%s: its own path was not measured' % label, a))
            continue
        if isinstance(o.get('inside'), list):                        # the probe looked into the opening itself
            # only a measured bought part or fastener says what the opening is for. A mesh, a body whose cut failed, a
            # hidden body or a printed neighbour that reaches into it does not: the opening is then judged like any other
            used = any(isinstance(n, dict) and n.get('kind') in ('bought', 'hardware') and not n.get('approx') and isinstance(n.get('name'), str) and n['name']
                       and (overlay is None or not overlay.search(n['name'])) for n in o['inside'])
        else:                                                        # older facts: a bought part or a fastener at the very mouth stands for it
            used = any(isinstance(h, dict) and h.get('kind') in ('bought', 'hardware') and _num(h.get('at')) and h['at'] <= gap + _EPS for h in path['hits'])
        if used:
            continue                                                 # something sits in it: that is what it is for
        s = _acc_sorted(path, reach, share, overlay)
        ledge = [h for h in s['self'] if not (_num(h.get('at')) and h['at'] <= gap + _EPS)]
        _acc_note(groups, 'overlay', s['overlay'], label)
        if ledge:
            first, names = _acc_first(ledge), _names(ledge)
            shares = [h['share'] for h in ledge if _num(h.get('share'))]
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                               '%s: the part itself stands in front of it %s mm out%s' % (label, _g(first), ' and covers %s of it' % _pct(max(shares)) if shares else ''),
                               anchor=a, measure=first, limit=reach, by=names,
                               context={'of': o.get('of'), 'path': 'own', 'free': first, 'by': names, 'kinds': ['self']}))
            continue
        if not (s['body'] or s['approx']):
            short = _acc_short(path, reach, label)
            if short:
                out.append(_not_run(ctx, short, a))
            continue
        _acc_note(groups, 'body', s['body'], label)
        _acc_note(groups, 'approx', s['approx'], label)
    return out + _acc_grouped(ctx, groups, reach, 'openings')


@judge
def access_screw_path(facts, params, ctx):
    """Rule access.screw_path. Reads fusion_openings facts: "openings"[] with "of" == "screw_head" and their
    "paths"["own"] (the section of the head) and "paths"["tool"] (the head plus access.tool_clearance, widened by the
    probe). Parameters access.reach, access.tool_clearance, access.min_blocked_share, access.seal_gap,
    access.overlay_pattern.
      the part itself, measured: FAIL per head, anchor kind "screw", measure = mm from the top of the head, limit =
        access.reach. It counts in the section of the head ("own" path) anywhere, and in the tool path when it begins
        farther out than access.seal_gap. The part itself in the ring of the tool clearance right at the top of the
        head is the wall of the recess the head sits in (a counterbore of head + 0.5 under a tool path of head + 1):
        not a ledge. One hit stands for the whole part, so a ledge higher up the same recess shows in the "own" path.
      another solid body in the tool path, measured: ONE FAIL row for the part with ask = "assembly.order" that lists
        every covering component nearest first (_acc_grouped), whatever the bodies are (a printed part, a motor):
        whether they are in the way depends on the assembly order. One question, answered once.
      a mesh or a body whose cut failed: one NOT_RUN row per body. A body of access.overlay_pattern: not counted.
    No tool path, a free path followed for less than the reach: NOT_RUN. With no "own" path the tool path stands for it."""
    reach, share, gap = pv(params, 'access.reach'), pv(params, 'access.min_blocked_share'), pv(params, 'access.seal_gap')
    openings, out = _rows(facts, 'openings', ctx)
    overlay = _acc_overlay(params, ctx, out)
    groups = {'body': {}, 'approx': {}, 'overlay': {}}
    for o in openings:
        if o.get('of') != 'screw_head':
            continue
        a, label = _anchor(o.get('anchor')), _acc_label(o, 'screw head')
        tool, own = _acc_path(o, 'tool'), _acc_path(o, 'own')
        if tool is None:
            out.append(_not_run(ctx, '%s: its "tool" path was not measured' % label, a))
            continue
        s = _acc_sorted(tool, reach, share, overlay)
        head = _acc_sorted(own, reach, share, overlay) if own is not None else s
        ledge = head['self'] or [h for h in s['self'] if not (_num(h.get('at')) and h['at'] <= gap + _EPS)]
        if ledge:
            first, names = _acc_first(ledge), _names(ledge)
            shares = [h['share'] for h in ledge if _num(h.get('share'))]
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                               '%s: the part itself stands over the head %s mm out%s: the screw or its key cannot go in'
                               % (label, _g(first), ' and covers %s of the path' % _pct(max(shares)) if shares else ''),
                               anchor=a, measure=first, limit=reach, by=names,
                               context={'of': o.get('of'), 'path': 'tool', 'free': first, 'by': names, 'kinds': ['self']}))
            continue
        _acc_note(groups, 'overlay', s['overlay'], label)
        if any(h.get('kind') == 'self' for h in head['approx']):
            out.append(_not_run(ctx, '%s: the part itself was only sampled with points over the head (its cut failed): not a measurement' % label, a))
            continue
        approx = [h for h in s['approx'] if h.get('kind') != 'self']
        if not (s['body'] or approx):
            short = _acc_short(tool, reach, label)
            if short:
                out.append(_not_run(ctx, short, a))
            continue
        _acc_note(groups, 'body', s['body'], label)
        _acc_note(groups, 'approx', approx, label)
    return out + _acc_grouped(ctx, groups, reach, 'screw heads')


@judge
def access_mouth_sealed(facts, params, ctx):
    """Rule access.mouth_sealed. Reads fusion_openings facts: "openings"[] whose "of" is hole, counterbore or
    insert_pocket, paths["own"].hits with "at", "share", "expected"; ctx["census"] to put holes on one axis together
    (_acc_bores). Parameters access.seal_gap, access.cap_max, access.overlay_pattern.
    An end is sealed by a hit that is not expected, at or under the seal gap, that covers the whole section (share >=
    0.95). A bore is sealed when EVERY one of its outer ends is: a through hole that lies against a plate at one end
    still takes its pin or insert from the other, and a screw that ends in the insert or thread of this part needs
    one end only. A plain screw clearance bore is the exception: the key comes to one end and the thread lies behind
    the other, so ONE sealed end closes it (a clearance hole flat against a body with no hole on its axis).
    Sealed, every sealing hit measured: FAIL "sealed by <name>", anchor = the first sealed end, measure = the nearest
    distance, limit = the seal gap, by = every sealing body.
    Sealed, and one end only by a mesh or by sample points: NOT_RUN. An end without its own path: NOT_RUN.
    Slots and windows are not judged here (what lies flat on a window is as a rule what it is for), nor screw heads.
    A body of access.overlay_pattern seals nothing.
    A bore of the census with no opening row at an outer end was not measured: NOT_RUN (_acc_no_end). For a bearing
    seat and a screw hole access.hole_covered has that row, for an insert pocket inserts.mouth_reachable, for every
    other hole this rule has it."""
    gap, cap = pv(params, 'access.seal_gap'), pv(params, 'access.cap_max')
    openings, out = _rows(facts, 'openings', ctx)
    overlay = _acc_overlay(params, ctx, out)
    bores, _ = _acc_bores(openings, ctx, cap, out)
    for b in bores:
        roles = {(_hole_class(h, ctx) or {}).get('role') for h in b['holes']}
        use = _acc_use(b, ctx, params)[0]
        clearance = use == 'screw' and not roles & {'insert_pocket', 'pilot', 'tap_pilot'}
        if not b['ends'] and b['holes'] and use is None and 'insert_pocket' not in roles:
            out.append(_acc_no_end(b, ctx))                          # (a seat or a screw hole without a row: access.hole_covered; a pocket: inserts.mouth_reachable)
        sealed, maybe, free = [], [], not b['ends']
        for o in b['ends']:
            label, path = _acc_label(o), _acc_path(o, 'own')
            if path is None:
                out.append(_not_run(ctx, '%s: its own path was not measured' % label, _anchor(o.get('anchor'))))
                free = True
                continue
            seals = [h for h in path['hits'] if isinstance(h, dict) and h.get('expected') is not True and _num(h.get('at'))
                     and h['at'] <= gap + _EPS and _num(h.get('share')) and h['share'] >= 0.95 - _EPS
                     and (h.get('kind') == 'self' or overlay is None or not overlay.search(str(h.get('name') or '')))]
            hard = [h for h in seals if not (h.get('approx') or h.get('by_box') or h.get('by_points') or h.get('kind') == 'mesh')]
            if hard:
                sealed.append((o, hard))
            elif seals:
                maybe.append((o, seals))
            else:
                free = True
        if not (sealed or maybe) or (free and not clearance):
            continue
        if maybe and not (clearance and sealed):
            o, hits = maybe[0]
            out.append(_not_run(ctx, '%s: it may be sealed by %s, which is known only as a mesh or by sample points: not a measurement'
                                % (_acc_label(o), ', '.join(_names(hits))), _anchor(o.get('anchor'))))
            continue
        o, hits = sealed[0]
        names = _names([h for _, hs in sealed for h in hs])
        at = min(h['at'] for _, hs in sealed for h in hs)
        text = '%s: sealed by %s, %s mm in front of the mouth' % (_acc_label(o), ', '.join(_names(hits)), _g(at))
        if len(sealed) > 1:
            text += '; its other end is sealed too, by %s' % ', '.join(_names([h for _, hs in sealed[1:] for h in hs]))
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text, anchor=_anchor(o.get('anchor')), measure=at, limit=gap, by=names,
                           context={'of': o.get('of'), 'sealed_by': names, 'at': at}))
    return out


@judge
def access_hole_closed(facts, params, ctx):
    """Rule access.hole_closed. Reads fusion_openings facts: "closed"[] ({"hole", "anchor", "cap", "behind": {"what":
    "hole" | "screw" | "ref", "name", "d"}}); ctx["census"] for the diameter of the hole at its blind end; ctx["print"]
    for the print axis. Parameters access.cap_max, overhang.bridge_step, overhang.max_angle.
    A blind end with a cap up to the limit in front of something that continues on the axis: FAIL, measure = cap,
    limit = access.cap_max, by = [what is behind]. A row without a cap, or facts that say ("cap_max") the probe looked
    through less plastic than the limit: NOT_RUN.
    One cap between two holes is one finding: the probe lists it from both sides, the row of the wider hole is kept.
    A bridge layer is a known print aid and no defect: a counterbore that faces the bed cannot print the ring round
    its narrower hole in the air, so one layer closes the hole and is opened after the print. A cap is taken for one
    when it lies between two holes of different diameters and is at most overhang.bridge_step thick. The facts cannot
    tell it from a leftover skin of the same thickness (a fill closed a hole with 0.2 mm once), so it is asked, not
    passed: INFO with ask = "access.bridge_layer", limit = the bridge step. When the print axis is known and the wider
    hole does not face the bed (flatter than overhang.max_angle), nothing needs a bridge there: FAIL as any other cap."""
    import math
    cap_max, step, angle = pv(params, 'access.cap_max'), pv(params, 'overhang.bridge_step'), pv(params, 'overhang.max_angle')
    closed, out = _rows(facts, 'closed', ctx)
    if _num(facts.get('cap_max')) and facts['cap_max'] < cap_max - _EPS:
        out.append(_not_run(ctx, 'not proven: blind ends were followed through %s mm of plastic, the limit is %s' % (_g(facts['cap_max']), _g(cap_max))))
    census = ctx.get('census') if isinstance(ctx.get('census'), dict) else {}
    ends = {h.get('id'): (_segments(h) or [{}])[-1].get('d') for h in _list(census.get('holes')) if isinstance(h, dict)}
    caps = {}
    for n, c in enumerate(closed):
        a, cap = _anchor(c.get('anchor')), c.get('cap')
        if not _num(cap):
            out.append(_not_run(ctx, 'hole %s at %s: the plastic behind its end was not measured' % (c.get('hole') or '', _at(a)), a))
            continue
        if cap > cap_max + _EPS:
            continue
        behind = c.get('behind') if isinstance(c.get('behind'), dict) else {}
        pair = behind.get('what') == 'hole' and isinstance(c.get('hole'), str) and isinstance(behind.get('name'), str)
        key = (tuple(sorted((c['hole'], behind['name']))), round(cap, 3)) if pair else n
        own = ends.get(c['hole']) if isinstance(c.get('hole'), str) else None
        if key not in caps or (_num(own) and not (_num(caps[key][3]) and caps[key][3] >= own)):
            caps[key] = (c, a, behind, own)
    up = ctx['print'].get('up') if isinstance(ctx.get('print'), dict) else None
    for key in caps:
        c, a, behind, own = caps[key]
        cap, other = c['cap'], behind.get('d')
        name = str(behind.get('name') or behind.get('what') or 'a coaxial feature')
        text = 'hole %s at %s ends in a %s mm cap; behind it on the same axis: %s %s D%s' % (c.get('hole') or '', _at(a), _g(cap), behind.get('what') or 'feature', name, _g(other))
        bridge = behind.get('what') == 'hole' and _num(own) and _num(other) and abs(own - other) > 0.05 and cap <= step + _EPS
        if bridge and a is not None and _xyz(up) and _num(angle):
            down = -sum(x * y for x, y in zip(a['axis'], up)) * (1.0 if own > other else -1.0)        # 1.0: the wider hole looks straight at the bed
            if down < math.sin(math.radians(angle)) - _EPS:
                bridge, text = False, text + '; the wider hole does not face the bed, so this is no bridge layer'
        if bridge:
            out.append(finding(ctx['rule'], ctx['part'], 'INFO', text + ' (own end D%s): one bridge step between two diameters. A bridge layer that is opened after the print, or a leftover skin?' % _g(own),
                               anchor=a, measure=cap, limit=step, by=[name], ask='access.bridge_layer',
                               context={'cap': cap, 'behind': behind.get('what'), 'd': other, 'own': own}))
            continue
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text,
                           anchor=a, measure=cap, limit=cap_max, by=[name], context={'cap': cap, 'behind': behind.get('what'), 'd': other}))
    return out


def _insert_band_of(d, through, depth, ctx):
    """The insert kind whose band holds a straight hole of diameter d (a through hole is in no "blind_only" band, a blind
    hole shallower than "min_depth" in none), or None. Kinds are tried in the order of their names."""
    kinds = _table(ctx, 'inserts').get('kinds') or {}
    for kind in sorted(kinds):
        band = (kinds[kind].get('band') or {}) if isinstance(kinds[kind], dict) else {}
        rng = band.get('d')
        if not (isinstance(rng, list) and len(rng) == 2 and _num(rng[0]) and _num(rng[1]) and _num(d)):
            continue
        if through and band.get('blind_only'):
            continue
        if not through and _num(band.get('min_depth')) and _num(depth) and depth < band['min_depth'] - _EPS:
            continue
        if rng[0] - _EPS <= d <= rng[1] + _EPS:
            return kind
    return None


def _pocket_len_max(d, ctx):
    """The length of the deepest pocket of the inserts table whose band holds the diameter d: hole depth plus relief
    depth plus the table's depth tolerance. None when no band holds it. A broken-open cylinder longer than this is no
    pocket."""
    table = _table(ctx, 'inserts')
    tol = (table.get('tol') or {}).get('depth') if isinstance(table.get('tol'), dict) else None
    best = None
    for row in (table.get('kinds') or {}).values():
        band = (row.get('band') or {}) if isinstance(row, dict) else {}
        rng, hole, relief = band.get('d'), (row.get('hole') or {}) if isinstance(row, dict) else {}, (row.get('relief') or {}) if isinstance(row, dict) else {}
        if not (isinstance(rng, list) and len(rng) == 2 and _num(rng[0]) and _num(rng[1]) and _num(d) and _num(hole.get('depth'))):
            continue
        if rng[0] - _EPS <= d <= rng[1] + _EPS:
            n = hole['depth'] + (relief['depth'] if _num(relief.get('depth')) else 0.0) + (tol if _num(tol) else 0.0)
            best = n if best is None else max(best, n)
    return best


def _some_names(names, n=3):
    """Names for the text of a finding: the first n, then how many more. The whole list stays in "by" or the context."""
    names = list(names)
    return ', '.join(names[:n]) + (' and %d more' % (len(names) - n) if len(names) > n else '')


def _content_asked(hole, c):
    """Does fits.bought_part_pocket ask "what goes in" about this hole? Then the names of the bought and hardware bodies
    on its axis (a list, empty for none), else None. It asks about a hole of no table (class None or role "unknown")
    that is blind, or that has such a body on its axis. A through hole of no table with nothing bought in it is asked
    by census.unknown_hole ("what is this hole"). One hole gets one question, never both."""
    if c is not None and c.get('role') != 'unknown':
        return None
    bought = sorted({str(x.get('name')) for x in _list(hole.get('coaxial')) if isinstance(x, dict) and x.get('kind') in ('bought', 'hardware') and x.get('name')})
    if not _segments(hole) or (hole.get('through') and not bought):
        return None
    return bought


def _named_screw(names, pattern):
    """Does one of the names (the bodies on a hole's axis) match the screw pattern? A pattern that does not compile
    matches nothing."""
    try:
        return any(re.search(pattern, n) for n in names)
    except (re.error, TypeError):
        return False


@judge
def census_unknown_hole(facts, params, ctx):
    """Attest rule census.unknown_hole. Reads fusion_census facts: "holes"[] and "partial"[]. Parameter
    screws.ref_pattern. One INFO finding with ask = "census.unknown_hole" per hole that has no known purpose:
    - its class is None or has role "unknown" (the class by today's tables, as in inserts_pocket_shape);
    - it is one straight cylinder classified as a screw clearance hole, its diameter lies in the band of an insert kind,
      and no screw ref lies on its axis: the old straight pocket of that insert and a clearance hole of the next thread
      size are the same hole (D4.4: an M4 clearance hole, and the datasheet pocket of an M3 insert);
    - it is a BLIND straight clearance hole with no screw ref on its axis: a clearance hole goes through;
    - "partial"[]: a cylinder wall broken open over more than a third of its circle (the census drops it from the
      holes) whose diameter lies in an insert band: a pocket broken out of the side of the part looks like this.
    anchor = the hole's anchor, context = the rounded segments + the names of what is coaxial (so an answer is asked
    again when the hole changes). Facts without a "partial" list (an older census) are judged without it.
    A pocket of the pockets table (_bought_pocket) is not asked: it is known. Parameters besides screws.ref_pattern:
    the "d" and "depth" parameters of every kind of that table.
    One hole gets one question. Not asked here:
    - a hole of no table that is blind, or has a bought or hardware body on its axis: fits.bought_part_pocket asks what
      goes in (_content_asked);
    - a hole that is only a piece of another hole of the census (_piece_of);
    - a blind clearance hole that goes on behind a skin into a wider hole of the same part ("behind"): the far half of
      a counterbored hole with a bridge layer; and its counterbore, which _hole_class names from the pair;
    - a broken-open cylinder longer than the deepest pocket of its diameter (_pocket_len_max): a channel, no pocket.
    A step of an unknown hole that has the diameter of a straight insert pocket is named in the question."""
    holes, out = _rows(facts, 'holes', ctx)
    pattern = pv(params, 'screws.ref_pattern')
    for h in holes:
        c = _hole_class(h, ctx)
        a, segs, coaxial = _anchor(h.get('anchor')), _segments(h), _names(h.get('coaxial'))
        why = None
        if _bought_pocket(h, params, ctx):           # a pocket for a bought part at its parameter's size is known: fits.bought_part_pocket
            continue
        if _piece_of(h, holes):                      # the same surface as another hole of the census: that hole is judged
            continue
        if c is None or c.get('role') == 'unknown':
            if _content_asked(h, c) is not None:     # one hole, one question: fits.bought_part_pocket asks what goes in
                continue
            why = 'fits no row of the tables'
            old = next((k for k in [_insert_band_of(s['d'], False, s['len'], ctx) for s in segs or [] if s.get('d2') is None] if k), None)
            if old:
                why += ' (one of its steps has the diameter of a straight pocket for insert %s)' % old
        elif c.get('table') == 'screws' and c.get('role') == 'clearance' and segs and len(segs) == 1 and not _named_screw(coaxial, pattern):
            band = _insert_band_of(segs[0]['d'], bool(h.get('through')), segs[0]['len'], ctx)
            behind = h.get('behind') if isinstance(h.get('behind'), dict) else {}
            if band:
                why = 'is an %s clearance hole by the screws table and a straight pocket in the band of insert %s, with no screw ref on its axis' % (c.get('kind'), band)
            elif not h.get('through') and _num(behind.get('cap')) and _num(behind.get('d')) and behind['cap'] < segs[0]['d'] < behind['d']:
                pass                                 # it goes on behind a skin into a wider hole: the far half of a counterbored hole
            elif not h.get('through'):
                why = 'is %s clearance size and blind, with no screw ref on its axis: a clearance hole goes through' % c.get('kind')
        if why is None:
            continue
        text = 'hole %s%s at %s %s' % (_shape(segs), ', through' if h.get('through') else '', _at(a), why)
        if coaxial:
            text += '; coaxial with %s' % _some_names(coaxial)
        out.append(finding(ctx['rule'], ctx['part'], 'INFO', text + ': what is this hole?', anchor=a, ask='census.unknown_hole',
                           context={'segments': [[s['d'], s['len']] for s in segs or []], 'through': bool(h.get('through')), 'coaxial': coaxial}))
    for p in _list(facts.get('partial')):
        if not isinstance(p, dict) or not _num(p.get('d')):
            continue
        band = _insert_band_of(p['d'], True, None, ctx)
        if not band:
            continue
        longest = _pocket_len_max(p['d'], ctx)
        if _num(p.get('len')) and _num(longest) and p['len'] > longest + _EPS:
            continue                                 # longer than the deepest pocket of that diameter: a channel or a rounded edge, no pocket
        a = _anchor(p.get('anchor'))
        out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                           'a D%s x %s cylinder at %s has its wall over only %s of the circle and lies in the band of insert %s: a pocket broken open, or what is it?' % (_g(p['d']), _g(p.get('len')), _at(a), _pct(p.get('arc')), band),
                           anchor=a, ask='census.unknown_hole', context={'partial': [p['d'], p.get('len')], 'arc': p.get('arc')}))
    return out


@judge
def inserts_no_tapped_plastic(facts, params, ctx):
    """Attest rule inserts.no_tapped_plastic. Reads fusion_census facts: "holes"[] whose class (by today's tables) has
    the role "tap_pilot" (a tap-drill size of the screws table) or "pilot" (a thread-forming pilot of the inserts
    table): a thread cut or formed in the print. One INFO finding with ask = "inserts.no_tapped_plastic" per hole;
    the answer names the exception. anchor = the hole's anchor, context = the rounded segments + the class kind.
    A pocket of the pockets table (_bought_pocket) is not asked: fits.bought_part_pocket judges it. Parameters: the "d"
    and "depth" parameters of every kind of that table."""
    holes, out = _rows(facts, 'holes', ctx)
    for h in holes:
        c = _hole_class(h, ctx)
        if not c or c.get('role') not in ('tap_pilot', 'pilot'):
            continue
        if _bought_pocket(h, params, ctx):           # a magnet pocket of tap-drill diameter holds no thread
            continue
        if _piece_of(h, holes):                      # the same surface as another hole of the census
            continue
        a, segs = _anchor(h.get('anchor')), _segments(h)
        what = 'a tap-drill hole for %s' % c.get('kind') if c['role'] == 'tap_pilot' else 'a pilot for a thread formed in the plastic (%s)' % c.get('kind')
        out.append(finding(ctx['rule'], ctx['part'], 'INFO', 'hole %s at %s is %s: a thread in the print. Which screw goes in, and why not an insert?' % (_shape(segs), _at(a), what),
                           anchor=a, ask='inserts.no_tapped_plastic',
                           context={'segments': [[s['d'], s['len']] for s in segs or []], 'kind': c.get('kind'), 'role': c.get('role')}))
    return out


# ---- pockets for bought parts (probe fusion_census, table pockets) ---------------------------------------------------------------

@judge
def fits_bought_pocket(facts, params, ctx):
    """Rule fits.bought_part_pocket. Reads fusion_census facts: "holes"[]. Table: pockets. Parameters: the "d" and
    "depth" parameters of every kind of that table, each with its tag as "<name>#tag".
    A pocket of the table (_bought_pocket) whose "d" or "depth" parameter is not M or U for the declared target: FAIL,
    by = the parameters that are D or S (no measure: the size may be right, it was never printed). A tag that was not
    handed over: NOT_RUN.
    A blind hole that fits no row of any table and no kind of the pockets table, and any hole of no table with a bought
    or hardware body on its axis: INFO with ask = "fits.pocket_content" (what goes in). anchor = the hole's anchor,
    context = the rounded segments, the kind and the tags (so a tag that changes opens it again).
    That hole is then not asked by census.unknown_hole as well (_content_asked): one hole, one question. A hole that is
    only a piece of another hole of the census (_piece_of) is not asked at all. The text names three of the bodies on
    the axis and counts the rest; "by" holds them all."""
    holes, out = _rows(facts, 'holes', ctx)
    for h in holes:
        a, segs = _anchor(h.get('anchor')), _segments(h)
        found = _bought_pocket(h, params, ctx)
        if found:
            kind, row = found
            names = [row['d'], row['depth']]
            tags = {n: params.get(n + '#tag') for n in names}
            if any(t not in ('M', 'U', 'D', 'S') for t in tags.values()):
                out.append(_not_run(ctx, 'pocket %s at %s is a %s pocket, but the tags of %s were not handed to the judge' % (_shape(segs), _at(a), kind, ', '.join(names)), anchor=a))
                continue
            bad = [n for n in names if tags[n] not in ('M', 'U')]
            if bad:
                out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                                   'pocket %s at %s receives %s and takes its size from %s: decided, never printed for this nozzle, material, hole compensation and layer height. Print the coupon first'
                                   % (_shape(segs), _at(a), row.get('receives') or 'a bought part (%s)' % kind, ', '.join('%s = %s (tag %s)' % (n, _g(params.get(n)), tags[n]) for n in bad)),
                                   anchor=a, by=bad,
                                   context={'kind': kind, 'segments': [[s['d'], s['len']] for s in segs], 'tags': sorted(tags.items())}))
            continue
        c = _hole_class(h, ctx)
        bought = _content_asked(h, c)
        if bought is None or _piece_of(h, holes):    # known by a table; or a through hole with nothing bought in it; or the same surface as another hole
            continue
        text = '%s hole %s at %s fits no row of the tables and no declared pocket parameter' % ('through' if h.get('through') else 'blind', _shape(segs), _at(a))
        if bought:
            text += '; on its axis: %s' % _some_names(bought)
        out.append(finding(ctx['rule'], ctx['part'], 'INFO', text + ': what goes in this pocket?', anchor=a, ask='fits.pocket_content', by=bought,
                           context={'segments': [[s['d'], s['len']] for s in segs], 'through': bool(h.get('through')), 'coaxial': bought}))
    return out


@judge
def print_z_layers(facts, params, ctx):
    """Rule print.z_layers. Reads fusion_census facts: "holes"[] (anchor, depth), ctx["print"] ("up": the print axis in
    the part frame, "bed": the height of the bed face along it) and ctx["target"]. Table: pockets. Parameters
    target.layer_height, target.initial_layer_print_height, print.z_clearance, print.z_layer_tol and the "d", "depth"
    and "part_h" parameters of the table.
    The layers of a print end at the first layer height and then at every layer height above it: with a first layer of
    0.2 and layers of 0.3 at 0.2, 0.5, 0.8, ... above the bed face. A face prints where it is modelled only when it
    lies on one of these heights (or on the bed).
    For every pocket of the table (_bought_pocket) whose axis is the print axis (within 1 degree):
    - its depth is not a whole number of layers (off by more than print.z_layer_tol): FAIL, measure = the depth,
      limit = the least whole-layer depth that holds the bought part with the clearance. A pocket that opens in the
      bed face is measured from the bed: its depth is the first layer plus whole layers;
    - else the depth rounded DOWN to whole layers is under the part height + print.z_clearance: FAIL, same limit;
    - else its mouth, or its floor, does not lie on a layer counted from the bed face: FAIL, measure = that height.
    A pocket across the print axis is not judged: its depth lies in the print plane.
    NOT_RUN, never a pass: the declared target does not say the layer height or the first layer height; the bed face of
    the part is not known (no build table names its orientation, or the census has no pose of the part); the part
    height was not handed over.
    No pocket of the table on the part: nothing to judge, a pass."""
    holes, out = _rows(facts, 'holes', ctx)
    pockets = []
    for h in holes:
        found = _bought_pocket(h, params, ctx)
        if found:
            pockets.append((h, found[0], found[1]))
    if not pockets:
        return out
    target, layer, first = ctx.get('target'), params.get('target.layer_height'), params.get('target.initial_layer_print_height')
    if not isinstance(target, dict) or not _num(target.get('layer_height')) or not _num(layer) or layer <= 0:
        return out + [_not_run(ctx, 'the declared target of the project does not say the layer height (gate/project.json "targets": "layer_height"): %d pocket(s) for a bought part were not judged' % len(pockets))]
    if not _num(target.get('initial_layer_print_height')) or not _num(first) or first <= 0:
        return out + [_not_run(ctx, 'the declared target of the project does not say the first layer height (gate/project.json "targets": "initial_layer_print_height"): the layers are counted from it, so %d pocket(s) for a bought part were not judged' % len(pockets))]
    frame = ctx.get('print') if isinstance(ctx.get('print'), dict) else {}
    up = frame.get('up')
    if not _xyz(up) or abs(sum(c * c for c in up) - 1.0) > 1e-3:
        return out + [_not_run(ctx, 'the bed face of this part is not known (%s): %d pocket(s) for a bought part were not judged. Name the part and its orientation in the build table of a registered .3mf project, and scan the document'
                               % (frame.get('why') or 'no print orientation was handed to the judge', len(pockets)))]
    tol, clear, bed = pv(params, 'print.z_layer_tol'), pv(params, 'print.z_clearance'), frame.get('bed')
    whole = lambda z: abs(z - round(z / layer) * layer) <= tol + _EPS                       # a whole number of layers
    on_layer = lambda z: abs(z) <= tol + _EPS or (z >= first - tol - _EPS and whole(z - first))   # a height above the bed where a layer ends
    near = lambda z: 0.0 if z < first / 2.0 else round(first + max(0, round((z - first) / layer)) * layer, 6)
    for h, kind, row in pockets:
        a, segs = _anchor(h.get('anchor')), _segments(h)
        depth = h.get('depth') if _num(h.get('depth')) else sum(s['len'] for s in segs)
        if a is None:
            out.append(_not_run(ctx, 'a %s pocket %s has no anchor: its direction is not known' % (kind, _shape(segs))))
            continue
        cos = sum(a['axis'][i] * up[i] for i in range(3))
        if abs(cos) < 0.99985:                       # more than 1 degree off the print axis
            continue
        part_h = params.get(row.get('part_h'))
        if not _num(part_h):
            out.append(_not_run(ctx, 'pocket %s at %s is a %s pocket, but the height of the bought part (%s) was not handed to the judge' % (_shape(segs), _at(a), kind, row.get('part_h')), anchor=a))
            continue
        need = part_h + clear
        mouth = (sum(a['point'][i] * up[i] for i in range(3)) - bed) if _num(bed) else None
        floor = None if mouth is None else (mouth - depth if cos > 0 else mouth + depth)   # the axis points out of the material at the mouth
        # a pocket that starts on the bed face is the first layer plus whole layers deep; any other one whole layers
        from_bed = mouth is not None and abs(min(mouth, floor)) <= tol + _EPS and abs(first - layer) > _EPS
        base = first if from_bed else 0.0
        k = max(-(-(need - base - tol - _EPS) // layer), 0 if from_bed else 1)              # the least count of layers that holds the part
        least = round(base + k * layer, 6)
        down = round(base + ((depth - base + tol + _EPS) // layer) * layer, 6) if depth + tol + _EPS >= base else 0.0
        context = {'kind': kind, 'depth': depth, 'layer': layer, 'part_h': part_h, 'clear': clear}
        if abs(first - layer) > _EPS:
            context['first'] = first
        what = row.get('receives') or 'a bought part (%s)' % kind
        if not whole(depth - base):
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                               'pocket %s at %s for %s is %s deep along the print axis: not a whole number of %s layers%s. It prints %s or %s; the part is %s high and needs %s'
                               % (_shape(segs), _at(a), what, _g(depth), _g(layer), ' above the %s first layer' % _g(first) if from_bed else '',
                                  _g(down), _g(down + layer), _g(part_h), _g(need)),
                               anchor=a, measure=depth, limit=least, context=context))
        elif down < need - _EPS:
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                               'pocket %s at %s for %s is %s deep (%d layers of %s%s): under the part height %s + %s'
                               % (_shape(segs), _at(a), what, _g(down), round((down - base) / layer), _g(layer), ' above the %s first layer' % _g(first) if from_bed else '',
                                  _g(part_h), _g(clear)),
                               anchor=a, measure=depth, limit=least, context=context))
        elif mouth is None:
            out.append(_not_run(ctx, 'pocket %s at %s: the extent of the part along the print axis is not known (no bounding box in the census), so its faces were not placed on layers' % (_shape(segs), _at(a)), anchor=a))
        elif not on_layer(mouth) or not on_layer(floor):
            off, word = (mouth, 'opens') if not on_layer(mouth) else (floor, 'has its floor')
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                               'pocket %s at %s for %s %s %s above the bed face: not where a layer ends (first layer %s, then %s each), so its depth of %s does not print as modelled'
                               % (_shape(segs), _at(a), what, word, _g(off), _g(first), _g(layer), _g(depth)),
                               anchor=a, measure=off, limit=near(off), context=dict(context, mouth=mouth)))
    return out


# ---- screws and washers (probe fusion_screws) ------------------------------------------------------------------------------------

def _thread_in_nothing(s, a, facts, params, need):
    """The text of the finding for a screw whose thread nothing receives ("engagement" 0.0, "into" None): what the
    facts hold about the place, and what to do. It names
      - the hole of this part the screw lies in ("fastener_holes"[] with this screw on the axis): for a screw whose
        thread ends in this part that is the hole its tip stands in;
      - the body the end of the thread stands in ("tip_at", else "tip_in"), when the screw unit wrote it;
      - the bought bodies on that axis that nothing recognised ("bodies" of those rows): an insert or a nut under a
        name no pattern knows looks like this;
      - the free room in front of the tip."""
    ref = s.get('ref')
    rows = [h for h in _list(facts.get('fastener_holes')) if isinstance(h, dict) and isinstance(ref, str) and h.get('screw') == ref]
    held = s.get('role') in ('thread', 'both')
    parts = []
    for h in rows[:2]:
        what = '%s %s%s at %s' % (str(h.get('role') or 'hole').replace('_', ' '), h.get('hole') or '', ' (%s)' % h['kind'] if h.get('kind') else '', _at(_anchor(h.get('anchor'))))
        parts.append(('its thread ends in the %s of this part' if held else 'it passes this part through the %s') % what)
    if not rows:
        parts.append('the facts name no hole of this part on its axis' if isinstance(facts.get('fastener_holes'), list) else 'these facts hold no list of the holes on its axis')
    at = s.get('tip_at') if isinstance(s.get('tip_at'), dict) else None
    if at is not None:
        parts.append('the end of its thread stands in %s' % (at.get('part') or at.get('name') or 'a body with no name'))
    elif isinstance(s.get('tip_in'), str) and s['tip_in']:
        parts.append('its tip is inside %s' % s['tip_in'])
    elif not held:
        parts.append('its thread ends beyond this part, in a body these facts do not name' if 'tip_at' not in s else 'no solid body stands round the end of its thread')
    bodies = sorted({str(n) for h in rows for n in _list(h.get('bodies'))})
    if bodies:
        parts.append('on that axis %s that nothing recognised: %s' % ('lies a bought body' if len(bodies) == 1 else 'lie bought bodies', ', '.join(bodies[:3]) + (' and %d more' % (len(bodies) - 3) if len(bodies) > 3 else '')))
    else:
        parts.append('no insert, no nut and no body with a hole narrower than the thread stands round it')
    room = s.get('tip_room')
    if _num(room):
        parts.append('%s mm of free room in front of the tip%s' % (_g(room), ' (looked no farther)' if s.get('tip_room_capped') else '') if room >= 0 else 'the tip is %s mm past the end of its hole' % _g(-room))
    return ('%s: nothing on its axis receives the thread: 0 mm of engagement, %s needs %s. %s. Model the insert or the nut it screws into as a ref body there, or name the body that is there '
            'so that its pattern matches (inserts.ref_pattern is %s, screws.nut_ref_pattern is %s)'
            % (_screw(s, a), s.get('size') or 'this size', _g(need), '; '.join(parts)[:1].upper() + '; '.join(parts)[1:], pv(params, 'inserts.ref_pattern'), pv(params, 'screws.nut_ref_pattern')))


@judge
def screws_engagement(facts, params, ctx):
    """Rule screws.engagement. Reads fusion_screws facts: "screws"[] ({"ref", "size", "length", "anchor", "engagement",
    "into", "role", "tip_room", "tip_at"}) and "fastener_holes"[]. Parameters screws.min_engagement (per size:
    pv(params, "screws.min_engagement", size)), inserts.ref_pattern, screws.nut_ref_pattern.
    engagement under the limit: FAIL. None on a screw that only has its head here (role "head"): nothing.
    None with any other role (the thread ends in this part, or the role is not given): NOT_RUN.
    engagement 0.0 with "into" None: the unit followed the whole thread and found no insert, no nut and no body with a
    hole narrower than the thread round it. That is a measurement of the model as drawn, not a lack of data: FAIL,
    never NOT_RUN and never a pass. Whether a ref is missing in the model or the screw really holds nothing, the model
    is what gets printed and built from, and the fix is in the model either way; a FAIL can also be waived by the
    owner with the reason (a thread in a bought housing that is not drawn), a NOT_RUN could not. The text says what
    the facts hold about the place and what to do (_thread_in_nothing)."""
    screws, out = _rows(facts, 'screws', ctx)
    for s in screws:
        a, e, size = _anchor(s.get('anchor')), s.get('engagement'), s.get('size')
        if e is None and s.get('role') == 'head':
            continue
        if not _num(e):
            out.append(_not_run(ctx, '%s: its thread ends in this part (role %s) and the engagement was not measured' % (_screw(s, a), s.get('role')), a))
            continue
        need = pv(params, 'screws.min_engagement', size if isinstance(size, str) else None)
        if e >= need - _EPS:
            continue
        into = s.get('into') if isinstance(s.get('into'), dict) else {}
        if not into and e <= _EPS:
            text = _thread_in_nothing(s, a, facts, params, need)
        else:
            text = '%s: %s mm of thread in %s, %s needs %s' % (_screw(s, a), _g(e), into.get('name') or into.get('kind') or 'its thread', size or 'this size', _g(need))
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text,
                           anchor=a, measure=e, limit=need, context={'size': size, 'length': s.get('length'), 'engagement': e, 'into': into.get('kind')}))
    return out


def _washer_on_steel(w, touch, washers=(), fasteners=()):
    """Does a washer of the fusion_screws facts bear on a bought rigid body and on no plastic: its landing is on a
    bearing ring ("landing"."on_bearing"), or the first row of "landing"."covers" is a hardware body within `touch`
    mm of it that is no fastener itself (not another washer of the facts: `washers` holds their refs; not a body whose
    name one of the patterns in `fasteners` knows: the rim of an insert, a nut, a screw head: these sit in plastic and
    pass the load of the head on into it); and no printed cover lies within `touch` (a washer that also lies on plastic
    loads that plastic).
    Proven means read: no landing in the facts, no "covers" list (facts of an older unit, also when "on_bearing" is
    set), or covers that cannot be read: False, nothing is proven and the screw is judged like any head."""
    land = w.get('landing') if isinstance(w, dict) else None
    if not isinstance(land, dict):
        return False
    covers = land.get('covers')
    if not (isinstance(covers, list) and all(isinstance(c, dict) for c in covers)):
        return False
    printed = lambda c: c.get('kind') == 'printed' or bool(c.get('part'))
    if any(printed(c) and not (_num(c.get('gap')) and c['gap'] > touch + _EPS) for c in covers):
        return False                                 # plastic in contact, or a printed cover whose gap the facts do not hold
    first = covers[0] if covers else None
    steel = (first is not None and not printed(first) and first.get('kind') == 'hardware' and first.get('name') not in washers
             and not any(_named_screw([first.get('name')], pat) for pat in fasteners)
             and _num(first.get('gap')) and first['gap'] <= touch + _EPS)
    return bool(land.get('on_bearing')) or steel


@judge
def screws_under_head(facts, params, ctx):
    """Rule screws.under_head. Reads fusion_screws facts: "screws"[].under_head (mm, None when the head is not on this
    part), .seat, .role, .washer and "washers"[].landing ({"on_bearing", "covers": [{"kind", "part", "gap"}]}).
    Parameters screws.min_under_head, washers.landing_gap_max, screws.ref_pattern, inserts.ref_pattern,
    screws.nut_ref_pattern. under_head under the limit: FAIL.
    The rule is about plastic that a head crushes. A head that bears, through its washer, on a bought rigid body and
    on no plastic (_washer_on_steel: a clamp washer on a bearing ring, a washer on a steel sleeve) loads no plastic
    under it: not judged here; washers.landing and bearings.clamp_room judge that clamp. A washer that lies on
    plastic, also beside a ring, is judged like any head; so is a washer on the rim of an insert, on a nut or on a
    screw head (the ref patterns name them), and a washer whose covers the facts do not list.
    None on a screw that only has its thread here (role "thread"): nothing. None with any other role: NOT_RUN."""
    need, touch = pv(params, 'screws.min_under_head'), pv(params, 'washers.landing_gap_max')
    fasteners = [pv(params, k) for k in ('screws.ref_pattern', 'inserts.ref_pattern', 'screws.nut_ref_pattern')]
    screws, out = _rows(facts, 'screws', ctx)
    washers = {w['id']: w for w in _list(facts.get('washers')) if isinstance(w, dict) and isinstance(w.get('id'), str)}
    for s in screws:
        a, u = _anchor(s.get('anchor')), s.get('under_head')
        if u is None and s.get('role') == 'thread':
            continue
        if not _num(u):
            out.append(_not_run(ctx, '%s: its head sits on this part (role %s) and the plastic under it was not measured' % (_screw(s, a), s.get('role')), a))
            continue
        if u >= need - _EPS:
            continue
        if isinstance(s.get('washer'), str) and _washer_on_steel(washers.get(s['washer']), touch, [w.get('ref') for w in washers.values()], fasteners):
            continue                                 # the head bears on steel through its washer: no plastic is loaded under it
        seat = s.get('seat') if isinstance(s.get('seat'), dict) else {}
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: %s mm of plastic under its head (%s seat), under %s' % (_screw(s, a), _g(u), seat.get('kind') or 'unknown', _g(need)),
                           anchor=a, measure=u, limit=need, context={'size': s.get('size'), 'under_head': u, 'seat': seat.get('kind'), 'recess': seat.get('recess')}))
    return out


@judge
def screws_tip_bottoming(facts, params, ctx):
    """Rule screws.tip_bottoming. Reads fusion_screws facts: "screws"[].tip_room (mm; negative = the tip is past the end
    of the hole; None = the hole has no end in front of the tip). Parameter screws.tip_room_min.
    tip_room under the limit: FAIL. No "tip_room" key, or a value that is neither a number nor None: NOT_RUN."""
    need = pv(params, 'screws.tip_room_min')
    screws, out = _rows(facts, 'screws', ctx)
    for s in screws:
        a, t = _anchor(s.get('anchor')), s.get('tip_room')
        if t is None and 'tip_room' in s:
            continue
        if not _num(t):
            out.append(_not_run(ctx, '%s: the room in front of its tip cannot be read' % _screw(s, a), a))
            continue
        if t >= need - _EPS:
            continue
        why = 'its tip is %s mm past the end of its hole' % _g(-t) if t < 0 else '%s mm of room in front of its tip, under %s' % (_g(t), _g(need))
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: %s' % (_screw(s, a), why), anchor=a, measure=t, limit=need,
                           context={'size': s.get('size'), 'length': s.get('length'), 'tip_room': t}))
    return out


@judge
def screws_hole_through(facts, params, ctx):
    """Rule screws.hole_through. Reads fusion_screws facts: "screws"[].in_solid ({"len", "name", "part", "at"}: the longest
    stretch of the shank whose core lies inside a printed body: the hole is closed or half closed there; "len" 0.0 =
    none; "at" = mm from the head seat to the near end of that stretch).
    Parameters screws.solid_shank_max, overhang.bridge_step. len over the limit: FAIL, measure = len, by = [the body]:
    the hole of that screw is closed there (a join or a fill grew over it, or it was never cut).
    One exception, and only where the facts prove it: a skin no thicker than overhang.bridge_step that begins right
    under the head (at <= screws.solid_shank_max) of a screw whose head sits in a counterbore is the sacrificial bridge
    of a counterbore that prints facing the bed: INFO with the bench step, not a FAIL (one number for that layer in every
    rule; the question "bridge layer or leftover skin" is asked once, by access.hole_closed). The same skin anywhere else
    along the shank, under a head on a flat face, under a head whose seat the facts do not hold, a thicker one, and
    facts without "at" (an older probe) fail as before.
    A screw without "in_solid" (facts of an older probe, or a ref with no readable shape): NOT_RUN."""
    limit, skin = pv(params, 'screws.solid_shank_max'), pv(params, 'overhang.bridge_step')
    screws, out = _rows(facts, 'screws', ctx)
    for s in screws:
        a, solid = _anchor(s.get('anchor')), s.get('in_solid')
        if not isinstance(solid, dict) or not _num(solid.get('len')):
            out.append(_not_run(ctx, '%s: it was not measured whether its shank runs in a hole' % _screw(s, a), a))
            continue
        if solid['len'] <= limit + _EPS:
            continue
        name = str(solid.get('part') or solid.get('name') or 'a printed body')
        seat = s.get('seat')
        in_counterbore = isinstance(seat, dict) and seat.get('kind') == 'counterbore'         # no seat in the facts is no proof of a counterbore
        if _num(solid.get('at')) and solid['at'] <= limit + _EPS and solid['len'] <= skin + _EPS and in_counterbore:
            out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                               '%s: a %s mm skin of %s closes the hole right under its head: the sacrificial bridge of a counterbore that prints facing the bed (up to %s). Drill it through at assembly and write that step into the build notes' % (_screw(s, a), _g(solid['len']), name, _g(skin)),
                               anchor=a, measure=solid['len'], limit=skin, by=[name], context={'size': s.get('size'), 'bridge_skin': solid['len'], 'by': name}))
            continue
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: %s mm of its shank lie in solid plastic of %s: the hole is closed there' % (_screw(s, a), _g(solid['len']), name),
                           anchor=a, measure=solid['len'], limit=limit, by=[name], context={'size': s.get('size'), 'in_solid': solid['len'], 'by': name}))
    return out


def _gap_along(c):
    """The two numbers of a body in a gap ("along" of an "in_gap" row of the fusion_screws facts): the air between the
    part on the head side and the body, and between the body and the part on the thread side, on one line parallel to
    the screw. [None, None] when the row does not hold them (facts written before the unit measured along the screw)."""
    along = c.get('along') if isinstance(c, dict) else None
    return list(along) if isinstance(along, list) and len(along) == 2 else [None, None]


def _gap_bridges(row, reach, touch):
    """The names of the bought bodies that bridge one air gap of the fusion_screws facts ("gaps"[] of a screw): a row
    of "in_gap" that stands within `reach` mm of the screw axis ("off") and on which BOTH parts press along the screw:
    on one line parallel to the screw the part on the head side ends on the body and the part on the thread side
    begins behind it, each within `touch` (the contact gap; "along"). Then the screw closes on steel: part, body,
    part, no air.
    Nothing else bridges. A ring that sits in a bore of one part with a fit clearance and has no shoulder behind it,
    a pin through both parts, a shaft, a plate that lies on one part and stops short of the other: one of the two
    numbers is over the contact gap or missing, and the screw closes air first.
    A body without both numbers bridges nothing (not measured is no contact)."""
    out = []
    for c in _list(row.get('in_gap')):
        along = _gap_along(c)
        if not isinstance(c, dict) or not _num(c.get('off')) or c['off'] > reach + _EPS or not all(_num(x) for x in along):
            continue
        if all(x <= touch + _EPS for x in along):
            out.append(str(c.get('name') or 'a bought body'))
    return sorted(set(out))


def _gap_sides(row, ids=False):
    """The two sides of a gap row (head side first) by name, or by part id where they have one (`ids`: what a context
    is made of), or None when "between" does not name both."""
    first, then = ('part', 'name') if ids else ('name', 'part')
    sides = [str(x.get(first) or x.get(then)) for x in _list(row.get('between')) if isinstance(x, dict) and (x.get('part') or x.get('name'))]
    return sides if len(sides) == 2 else None


def _gap_between(row):
    """' between Lid and Housing' for a gap row whose "between" names its two sides (head side first), else ''."""
    sides = _gap_sides(row)
    return ' between %s and %s' % tuple(sides) if sides else ''


def _gap_loose(c, row, touch):
    """Why a body that stands in a gap does not bridge it, as text: what each of the two parts does to it along the
    screw."""
    sides = _gap_sides(row) or ['the part on the head side', 'the part on the thread side']
    if sides[0] == sides[1]:                         # one part on both sides of the gap: a hollow in it
        sides = ['%s on the head side' % sides[0], '%s on the thread side' % sides[1]]
    along = _gap_along(c)
    if c.get('late'):
        return 'what the two parts do to it along the screw was not measured (the time of that call was used up): collect the facts again'
    if 'along' not in c:
        return 'what the two parts do to it along the screw is not in these facts: collect the facts again'
    def one(who, x):
        if not _num(x):
            return '%s is not met on a line along the screw through it' % who
        return '%s presses on it' % who if x <= touch + _EPS else '%s stands %s mm off it along the screw' % (who, _g(x))
    return '%s, %s (contact is within %s)' % (one(sides[0], along[0]), one(sides[1], along[1]), _g(touch))


@judge
def screws_shell_bears(facts, params, ctx):
    """Rule screws.shell_bears. Reads fusion_screws facts: "screws"[].air_gap (mm of air between the clamped parts along
    the screw, None when the head is not on this part), .role, .washer, .gaps ([{"gap", "between": [who, who],
    "search_r", "in_gap": [{"name", "kind", "off", "along": [mm, mm], "late"}]}]: what stands in each gap) and
    "washers"[].landing.on_bearing.
    Parameters screws.air_gap_max, screws.bridge_search_r, bearings.contact_gap_max.
    air_gap over the limit: FAIL, measure = the gap. Two gaps are no air a wall bends into:
      - the screw of a clamp washer that bears on a bearing ring is left out (its gap is the clamp room:
        bearings.clamp_room and washers.landing own it);
      - a gap that a bought solid body bridges (_gap_bridges: it stands in the gap within screws.bridge_search_r of
        the axis and both parts press on it along the screw, each within bearings.contact_gap_max on one line: a
        bearing ring the two parts clamp between them). The load of that screw goes through the body. No FAIL; ONE
        INFO row for the part counts these screws and names the body and the two parts, so the exception can be
        checked by eye. The ring itself is judged by the bearing rules.
    A body that stands in the gap and that the two parts do NOT both press on bridges nothing: the gap is air the
    screws close first. The screws of one part with the same body in the same gap have one cause and are ONE FAIL
    row (count, the gap, the body, what each part does to it along the screw; measure = the widest gap; context =
    the body, the two sides, the screws and both distances, so a waiver holds only while all of that stays).
    Never a silent pass on what was not looked at:
      - facts of an older unit (a screw with a gap and no "gaps" list): the FAIL stays, one row per screw, and its
        text says that a ring clamped between the two parts would make this gap its clamp room, and to collect the
        facts again;
      - a gap whose body was not measured along the screw, or that was looked into over a smaller radius than today's
        parameter with nothing found: the FAIL stays and says so.
    A screw with several gaps is judged by its widest gap that nothing bridges.
    None on a screw that only has its thread here (role "thread"): nothing. None with any other role: NOT_RUN.
    (The wall round the screw, screws.local_wall_min, is not measured by the probe and not judged here.)"""
    limit, reach, touch = pv(params, 'screws.air_gap_max'), pv(params, 'screws.bridge_search_r'), pv(params, 'bearings.contact_gap_max')
    screws, out = _rows(facts, 'screws', ctx)
    clamps = {w['id'] for w in _list(facts.get('washers')) if isinstance(w, dict) and isinstance(w.get('id'), str)
              and isinstance(w.get('landing'), dict) and w['landing'].get('on_bearing')}
    bridged, held = [], {}
    for s in screws:
        a, gap = _anchor(s.get('anchor')), s.get('air_gap')
        if gap is None and s.get('role') == 'thread':
            continue
        if not _num(gap):
            out.append(_not_run(ctx, '%s: its head sits on this part (role %s) and the air along it was not measured' % (_screw(s, a), s.get('role')), a))
            continue
        if gap <= limit + _EPS or (isinstance(s.get('washer'), str) and s['washer'] in clamps):
            continue
        rows, more, between = s.get('gaps'), '', ''
        if not isinstance(rows, list):
            more = ('. These facts do not say what stands in that gap (they come from an older screw unit): if a bearing ring or another steel part is clamped '
                    'between the two parts this gap is its clamp room: collect the facts again')
        else:
            rows = [r for r in rows if isinstance(r, dict) and _num(r.get('gap')) and r['gap'] > limit + _EPS]
            if not rows:                             # a gap over the limit and no row for it: the list does not cover what was measured
                more = '. The facts hold no row for that gap, so what stands in it is not known: collect the facts again'
            else:
                open_ = [r for r in rows if not _gap_bridges(r, reach, touch)]
                if not open_:
                    bridged.append((s, a, max(rows, key=lambda r: r['gap'])))
                    continue
                worst = max(open_, key=lambda r: r['gap'])
                gap = worst['gap']
                between = _gap_between(worst)
                loose = [c for c in _list(worst.get('in_gap')) if isinstance(c, dict)]
                if loose:                            # one cause for every screw with this body in this gap: one row
                    held.setdefault((_names(loose[:1])[0], tuple(_gap_sides(worst, ids=True) or ())), []).append((s, a, worst, loose[0]))
                    continue
                if _num(worst.get('search_r')) and worst['search_r'] < reach - _EPS:
                    more = '. The gap was looked into only %s mm round the screw axis, the rule counts %s: collect the facts again' % (_g(worst['search_r']), _g(reach))
                else:
                    more = '. Nothing bought stands in the gap within %s mm of the screw axis' % _g(reach)
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s clamps across %s mm of air%s (at most %s): the wall bends until it cracks%s' % (_screw(s, a), _g(gap), between, _g(limit), more),
                           anchor=a, measure=gap, limit=limit, context={'size': s.get('size'), 'air_gap': gap}))
    key = lambda t: gate_features.anchor_key(t[1]) if t[1] else str(t[0].get('ref'))
    for (name, sides) in sorted(held):
        group = sorted(held[(name, sides)], key=lambda t: -t[2]['gap'])       # the widest first, else in the order of the facts
        s, a, row, c = group[0]
        n = len(group)
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                           '%s clamp%s across %s mm of air%s (at most %s). %s stands in that gap and does not carry %s: %s. The screw%s close%s that air first: the wall bends until it cracks, '
                           'or the body is never clamped. Let both parts land on the body (the part that stands off needs its shoulder at the body), or close the gap%s'
                           % ('%d screws of this part' % n if n > 1 else _screw(s, a), '' if n > 1 else 's', _g(row['gap']), _gap_between(row), _g(limit), name,
                              'them' if n > 1 else 'it', _gap_loose(c, row, touch), 's' if n > 1 else '', '' if n > 1 else 's',
                              '. First: %s' % _screw(s, a) if n > 1 else ''),
                           anchor=a, measure=row['gap'], limit=limit, by=[name],
                           context={'in_gap': name, 'between': list(sides), 'screws': sorted(key(t) for t in group), 'air_gap': row['gap'], 'along': _gap_along(c)}))
    if bridged:
        s, a, row = max(bridged, key=lambda t: t[2]['gap'])
        by = sorted({n for t in bridged for r in _list(t[0].get('gaps')) if isinstance(r, dict) for n in _gap_bridges(r, reach, touch)})
        n = len(bridged)
        out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                           '%d screw%s of this part clamp%s across a gap that a bought body bridges, the widest %s mm%s: %s stands in it and both parts press on it along the screw (each within %s, at most %s mm from the screw axis). '
                           'That gap is the clamp room of that body, not air: the screws close on it. First: %s. What holds the body is judged by the bearing rules'
                           % (n, '' if n == 1 else 's', 's' if n == 1 else '', _g(row['gap']), _gap_between(row),
                              ', '.join(by[:3]) + (' and %d more' % (len(by) - 3) if len(by) > 3 else ''), _g(touch), _g(reach), _screw(s, a)),
                           measure=row['gap'], limit=limit, by=by,
                           context={'bridged': sorted(key(t) for t in bridged), 'by': by}))
    return out


@judge
def washers_landing(facts, params, ctx):
    """Rule washers.landing. Reads fusion_screws facts: "washers"[] ({"ref", "od", "anchor", "landing": {"share", "gap",
    "on", "spot_face_d", "open_d", "on_bearing", "covers": [{"name", "kind", "part", "share", "gap"}]}}).
    Parameters washers.landing_share_min, washers.landing_gap_max, washers.spot_face_clearance, bearings.clamp_room,
    washers.slot_land_min.

    A plain washer: gap over the maximum or share under the minimum: FAIL "does not lie on its landing" (one finding;
    measure = the gap in mm, else the share). "share" is the share of the washer's ring OUTSIDE the opening it sits
    over ("open_d": its screw hole, a pocket mouth with its relief), so a washer that bridges a relief and lands on
    the face round it passes, and one whose rim hangs over an edge fails.
    A washer over a slot of the part (a census opening of shape "slot" under it: a belt-tension slot) bears on the two
    flanks only. It passes when it reaches at least washers.slot_land_min over each flank and every sample circle
    ("ring_shares") is on material over the share that lies beside a slot of that width (_bridges_slot); with one flank
    gone, over an edge that is no slot, or over a slot nearly as wide as itself, it fails as before.
    A clamp washer ("on_bearing": the bearing ref it bears on) is meant to bridge: it bears on its ring and must NOT
    land on anything else. The share rule is not applied to it. Every other cover nearer to its face than
    bearings.clamp_room is a FAIL: measure = that gap (the set-back that is there), limit = bearings.clamp_room,
    by = [the cover]. This is the washer's own view of bearings.clamp_room and does not depend on where the ring was
    sampled. A clamp washer without its "covers" list: NOT_RUN.
    A spot face narrower than od + clearance: FAIL, measure = the spot face diameter.
    A washer without landing numbers: NOT_RUN."""
    share_min, gap_max, clearance = pv(params, 'washers.landing_share_min'), pv(params, 'washers.landing_gap_max'), pv(params, 'washers.spot_face_clearance')
    room, land_min = pv(params, 'bearings.clamp_room'), pv(params, 'washers.slot_land_min')
    washers, out = _rows(facts, 'washers', ctx)
    for w in washers:
        a, land, od = _anchor(w.get('anchor')), w.get('landing'), w.get('od')
        label = 'washer %s D%s at %s' % (w.get('ref') or w.get('id') or '', _g(od), _at(a))
        if not isinstance(land, dict) or not _num(land.get('share')) or not _num(land.get('gap')):
            out.append(_not_run(ctx, '%s: its landing was not measured' % label, a))
            continue
        on = land.get('on') or 'nothing'
        ring = land.get('on_bearing')
        if ring:
            covers = land.get('covers')
            if not isinstance(covers, list):
                out.append(_not_run(ctx, '%s clamps %s: what else lies under it was not measured' % (label, ring), a))
            else:
                for c in covers:
                    if not isinstance(c, dict) or c.get('bearing') or _names([c])[0] == ring:
                        continue
                    if not _num(c.get('gap')):
                        out.append(_not_run(ctx, '%s clamps %s: the gap to %s under it was not measured' % (label, ring, _names([c])[0]), a))
                    elif c['gap'] < room - _EPS:
                        name = str(c.get('part') or _names([c])[0])
                        out.append(finding(ctx['rule'], ctx['part'], 'FAIL',
                                           '%s clamps %s and has %s only %s mm under it (it needs %s): it lands on the plastic beside the ring and the ring stays loose' % (label, ring, name, _g(c['gap']), _g(room)),
                                           anchor=a, measure=c['gap'], limit=room, by=[name], context={'od': od, 'clamps': ring, 'beside': name, 'gap': c['gap']}))
        elif land['gap'] > gap_max + _EPS:
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s does not lie on its landing: %s mm of air to %s' % (label, _g(land['gap']), on),
                               anchor=a, measure=land['gap'], limit=gap_max, context={'od': od, 'gap': land['gap'], 'share': land['share'], 'on': on}))
        elif land['share'] < share_min - _EPS and _bridges_slot(w, land, ctx, land_min):
            pass                                     # it spans a slot of the part and bears on both flanks: what a washer over a slot is for
        elif land['share'] < share_min - _EPS:
            over = ' round the D%s opening under it' % _g(land['open_d']) if _num(land.get('open_d')) else ''
            slot = _slot_under(w, ctx) if _num(od) else None
            if slot is not None and (od - slot['width']) / 2.0 < land_min - _EPS:
                over = ' beside the slot %s mm wide under it (the washer reaches %s mm over each flank, a washer over a slot needs %s)' % (
                    _g(slot['width']), _g(max(0.0, (od - slot['width']) / 2.0)), _g(land_min))
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s does not lie on its landing: only %s of its ring%s is on %s, it needs %s' % (label, _pct(land['share']), over, on, _pct(share_min)),
                               anchor=a, measure=land['share'], limit=share_min, unit='share', context={'od': od, 'gap': land['gap'], 'share': land['share'], 'on': on}))
        spot = land.get('spot_face_d')
        if spot is not None and not (_num(spot) and _num(od)):
            out.append(_not_run(ctx, '%s: its spot face or its own diameter cannot be read' % label, a))
        elif spot is not None and spot < od + clearance - _EPS:
            out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s: its spot face is D%s, it needs D%s (the washer + %s)' % (label, _g(spot), _g(od + clearance), _g(clearance)),
                               anchor=a, measure=spot, limit=round(od + clearance, 4), context={'od': od, 'spot_face_d': spot}))
    return out


@judge
def washers_proud(facts, params, ctx):
    """Rule washers.proud. Reads fusion_screws facts: "washers"[].proud ({"height", "nearest": {"name", "kind", "part",
    "gap"} or None}). Parameter washers.gap_to_neighbour. nearest.gap under the limit: FAIL, by = [nearest.name].
    nearest None = nothing stands near: nothing. A washer without "proud", or a nearest body without a gap: NOT_RUN."""
    need = pv(params, 'washers.gap_to_neighbour')
    washers, out = _rows(facts, 'washers', ctx)
    for w in washers:
        a, proud = _anchor(w.get('anchor')), w.get('proud')
        label = 'washer %s D%s at %s' % (w.get('ref') or w.get('id') or '', _g(w.get('od')), _at(a))
        if not isinstance(proud, dict) or 'nearest' not in proud:
            out.append(_not_run(ctx, '%s: what stands near it was not measured' % label, a))
            continue
        near = proud['nearest']
        if near is None:
            continue
        if not isinstance(near, dict) or not _num(near.get('gap')):
            out.append(_not_run(ctx, '%s: the gap to its nearest neighbour cannot be read' % label, a))
            continue
        if near['gap'] >= need - _EPS:
            continue
        name = _names([near])[0]
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s stands %s mm proud with its head and comes within %s mm of %s, under %s' % (label, _g(proud.get('height')), _g(near['gap']), name, _g(need)),
                           anchor=a, measure=near['gap'], limit=need, by=[name], context={'height': proud.get('height'), 'gap': near['gap'], 'by': name}))
    return out


def _fastener_holes(facts, params, ctx):
    """The screw holes and insert pockets of the part with what is on the axis of each:
    [{"hole", "role", "anchor", "screw": a recognised screw on the axis or None, "bodies": [bought bodies on the axis
    that nothing recognised]}], or None when neither the screw facts nor the census can say.
    From the probe ("fastener_holes") when it wrote them. Facts of an older probe: from the census holes of class
    clearance, counterbore, countersink or insert pocket; a screw is on the axis when a row of "screws" or "passing"
    shares it (_on_axis); "bodies" are the coaxial bodies that are not printed and whose own name (the part behind
    " / ") no washer, insert, nut, bearing, dowel or pin pattern knows. The screw pattern is left out on purpose: a body
    that is named like a screw and has no screw row on its axis is exactly what the probe missed."""
    rows = facts.get('fastener_holes')
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    census = ctx.get('census')
    if not isinstance(census, dict) or not isinstance(census.get('holes'), list):
        return None
    pats = [pv(params, n) for n in ('washers.ref_pattern', 'inserts.ref_pattern', 'screws.nut_ref_pattern', 'bearings.ref_pattern')] + [r'(?i)dowel|\bpin\b']
    seen = [_anchor(s.get('anchor')) for key in ('screws', 'passing') for s in _list(facts.get(key)) if isinstance(s, dict)]
    out = []
    for h in census['holes']:
        c = _hole_class(h, ctx) if isinstance(h, dict) else None
        if not c or c.get('role') not in ('clearance', 'counterbore', 'countersink', 'insert_pocket'):
            continue
        a = _anchor(h.get('anchor'))
        names = _names([x for x in _list(h.get('coaxial')) if isinstance(x, dict) and x.get('kind') in ('bought', 'hardware')])
        out.append({'hole': h.get('id'), 'role': c.get('role'), 'anchor': h.get('anchor'),
                    'screw': 'a screw' if any(_on_axis(a, s) for s in seen if s) else None,
                    'bodies': [n for n in names if not any(_named_screw([n.rsplit(' / ', 1)[-1]], p) for p in pats)]})     # the body's own name, not its component's
    return out


@judge
def hardware_recognised(facts, params, ctx):
    """Rule hardware.recognised. Reads fusion_screws facts: "fastener_holes"[] (else the census holes of ctx, see
    _fastener_holes), "screws"[], "passing"[], "washers"[].screw, "n_screws", "rejected"[].
    Parameters: the ref patterns (screws, washers, inserts, nuts, bearings).
    A blind probe must never read as a part without screws. So:
      - screw holes or insert pockets with a bought body on their axis that nothing recognised, and no recognised
        screw there: ONE NOT_RUN row for the part, with the count, the first places and the names;
      - washers on this part whose screw is no row of "screws": one NOT_RUN row (the head sits on the washer);
      - no "fastener_holes" in the facts and no census in the context: NOT_RUN, the comparison cannot be made;
      - bodies a name pattern picked and the geometry refused ("rejected"): one INFO row, so the count can be compared.
    Never FAIL: what a probe did not recognise is not a measured defect of the part."""
    screws, out = _rows(facts, 'screws', ctx)
    holes = _fastener_holes(facts, params, ctx)
    if holes is None:
        out.append(_not_run(ctx, 'the screw holes of the part cannot be compared with the screws the probe recognised: no "fastener_holes" in the screw facts and no census of the part'))
        holes = []
    blind = [h for h in holes if not h.get('screw') and _list(h.get('bodies'))]
    if blind:
        names = sorted(set(str(n) for h in blind for n in h['bodies']))
        places = ', '.join('%s at %s' % (h.get('hole') or 'hole', _at(_anchor(h.get('anchor')))) for h in blind[:4]) + (' and %d more' % (len(blind) - 4) if len(blind) > 4 else '')
        n_near = facts.get('n_screws') if _num(facts.get('n_screws')) else len(screws)
        out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                           'the part has %d screw hole%s or insert pocket%s with a bought body on the axis that nothing recognised (%s), and the screw probe recognised %d screw%s near the part, none of them there. On these axes: %s. '
                           'Name the body so that its ref pattern matches (screws.ref_pattern is %s) or model the screw as a head on a shank; until then no screw rule has looked at these holes' % (
                               len(blind), '' if len(blind) == 1 else 's', '' if len(blind) == 1 else 's', places, n_near, '' if n_near == 1 else 's',
                               ', '.join(names[:6]) + (' and %d more' % (len(names) - 6) if len(names) > 6 else ''), pv(params, 'screws.ref_pattern')),
                           context={'blind': sorted(str(h.get('hole')) for h in blind), 'bodies': names}))
    refs = set(str(s.get('ref')) for s in screws)
    lone = [w for w in _list(facts.get('washers')) if isinstance(w, dict) and isinstance(w.get('screw'), str) and w['screw'] not in refs]
    if lone:
        places = ', '.join('%s at %s' % (w.get('ref') or w.get('id') or 'washer', _at(_anchor(w.get('anchor')))) for w in lone[:4]) + (' and %d more' % (len(lone) - 4) if len(lone) > 4 else '')
        out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN',
                           '%d washer%s on this part: the screw on %s is no row of the screws of the part (%s; first screw: %s). The head sits on the washer and was not measured: collect the screw facts again' % (
                               len(lone), ' lies' if len(lone) == 1 else 's lie', 'it' if len(lone) == 1 else 'each', places, lone[0]['screw']),
                           context={'lone_washers': sorted(str(w.get('ref') or w.get('id')) for w in lone)}))
    rejected = [r for r in _list(facts.get('rejected')) if isinstance(r, dict)]
    if rejected:
        r = rejected[0]
        out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                           '%d bod%s near the part %s picked by a hardware name pattern and refused by the geometry (%s: %s): not judged as hardware' % (
                               len(rejected), 'y' if len(rejected) == 1 else 'ies', 'was' if len(rejected) == 1 else 'were', r.get('name') or 'unnamed', r.get('why') or 'no reason given'),
                           context={'rejected': sorted(str(x.get('name')) for x in rejected)}))
    return out


# ---- clearance (probe fusion_gap) -----------------------------------------------------------------------------------------------------

@judge
def clearance_min_gap_moving(facts, params, ctx):
    """Rule clearance.min_gap_moving. Reads fusion_gap facts: "gaps"[] ({"name", "kind", "part", "group", "gap", "point",
    "dir", "approx"}), "margin", "own_group". Parameters clearance.min_gap_moving, clearance.scan_margin.
    A MEASURED gap under the limit: FAIL, anchor kind "face" at point / dir, by = [name]; "0 mm3 of overlap" plays no
    part. A gap of 0 says in its text that parts fastened to each other belong to one motion group.
    A gap that is not measured ("approx": a mesh body known by its bounding box, or a measurement that failed and fell
    back to the boxes) is never a FAIL. The distance between two boxes is never more than the real gap: at the limit or
    above it is a pass, under the limit it decides nothing and is NOT_RUN with what to do.
    margin below the limit, no "own_group" key, or a neighbour without a gap: NOT_RUN.
    own_group None (the part is in no declared motion group): nothing is measured, and one INFO finding says so (it
    does not block): either "the document declares no motion group at all" ("groups": 0) or "this part matches none of
    the declared groups" ("groups" above 0). Facts without "groups" (written before the probe counted them): nothing."""
    limit = pv(params, 'clearance.min_gap_moving')
    if not isinstance(facts, dict) or 'own_group' not in facts:
        return [_not_run(ctx, 'the facts do not say which motion group the part is in: the probe did not look')]
    if facts['own_group'] is None:
        if facts.get('groups') == 0:
            return [finding(ctx['rule'], ctx['part'], 'INFO', 'no motion group is declared for this document in gate/layout.json ("motion_groups"): the gap to moving neighbours was NOT checked for any part',
                            context={'groups': 0})]
        if _num(facts.get('groups')) and facts['groups'] > 0:
            return [finding(ctx['rule'], ctx['part'], 'INFO', 'this part matches none of the %d motion groups of gate/layout.json: its gap to moving neighbours was NOT checked. '
                            'A part that moves with others, or stands still with others, belongs to their group' % facts['groups'], context={'groups': facts['groups'], 'own_group': None})]
        return []
    gaps, out = _rows(facts, 'gaps', ctx)
    if not _num(facts.get('margin')) or facts['margin'] < limit - _EPS:
        out.append(_not_run(ctx, 'not proven: neighbours were listed only within %s mm, the limit is %s' % (_g(facts.get('margin')), _g(limit))))
    for g in gaps:
        a, name = _face(g.get('point'), g.get('dir')), _names([g])[0]
        if not _num(g.get('gap')):
            out.append(_not_run(ctx, 'the gap to %s was not measured' % name, a))
            continue
        if g['gap'] >= limit - _EPS:
            continue
        if g.get('approx'):                                         # two bounding boxes, not the bodies: under the limit this decides nothing
            what = 'the measurement failed (%s)' % str(g['error'])[:80] if g.get('error') else 'it is a mesh body, known by its bounding box only'
            out.append(finding(ctx['rule'], ctx['part'], 'NOT_RUN', 'the gap to %s (group %s) is not measured: %s, and the boxes come within %s mm at %s (limit %s). '
                               'Measure it in the model, or give the neighbour a solid body' % (name, g.get('group'), what, _g(g['gap']), _at(a), _g(limit)),
                               anchor=a, by=[name], context={'not_run': 'approx', 'by': name, 'group': g.get('group')}))
            continue
        text = 'gap %s mm to %s (group %s) at %s, under %s' % (_g(g['gap']), name, g.get('group'), _at(a), _g(limit))
        if g['gap'] <= _EPS:
            text += '; they touch. Parts that are fastened to each other move together: then they belong to ONE motion group in gate/layout.json'
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text, anchor=a, measure=g['gap'], limit=limit, by=[name],
                           context={'gap': g['gap'], 'by': name, 'group': g.get('group')}))
    return out


# ---- mesh (probe stl_mesh) -------------------------------------------------------------------------------------------------------------

@judge
def mesh_closed(facts, params, ctx):
    """Rule mesh.closed. Reads stl_mesh facts: "open_edges", "non_manifold_edges". Either above 0: one FAIL,
    measure = the two counts together, limit 0, unit "edges". A count that is not a number: NOT_RUN."""
    open_e, bad_e = facts.get('open_edges'), facts.get('non_manifold_edges')
    if not (_num(open_e) and _num(bad_e)):
        return [_not_run(ctx, 'the mesh facts hold no edge counts')]
    if open_e <= 0 and bad_e <= 0:
        return []
    return [finding(ctx['rule'], ctx['part'], 'FAIL', 'the STL is not closed: %d open and %d non-manifold edges' % (open_e, bad_e),
                    measure=open_e + bad_e, limit=0, unit='edges', context={'open_edges': open_e, 'non_manifold_edges': bad_e})]


@judge
def mesh_one_shell(facts, params, ctx):
    """Rule mesh.one_shell. Reads stl_mesh facts: "shells". Not 1: FAIL with the count, limit 1, unit "shells".
    A count that is not a number: NOT_RUN."""
    shells = facts.get('shells')
    if not _num(shells):
        return [_not_run(ctx, 'the mesh facts hold no shell count')]
    if shells == 1:
        return []
    return [finding(ctx['rule'], ctx['part'], 'FAIL', 'the STL has %d shells, a part is one' % shells, measure=shells, limit=1, unit='shells',
                    context={'shells': shells})]


# ---- project (probe project_consistency; ctx["part"] is "<project key>:" and each finding sets its own part) -------------------------

@judge
def project_stl_without_stamp(facts, params, ctx):
    """Rule project.stl_without_stamp. Reads project_consistency facts: "stls"[] ({"path", "part", "stamp": "valid" |
    "stale" | "none", "why"}). stamp "stale" or "none": FAIL; any other value but "valid": NOT_RUN. The finding's part
    is the STL's part id, or "<project key>:<file name>" when no part is known."""
    stls, out = _rows(facts, 'stls', ctx)
    for s in stls:
        part, state = _project_part(facts, s, _basename(s.get('path'))), s.get('stamp')
        if state == 'valid':
            continue
        if state not in ('stale', 'none'):
            out.append(_not_run(ctx, 'STL %s: its stamp was not looked up' % s.get('path'), part=part))
            continue
        why = 'has no stamp: it was not written by gate.py export' if state == 'none' else 'has a stale stamp'
        out.append(finding(ctx['rule'], part, 'FAIL', 'STL %s %s%s' % (s.get('path'), why, ' (%s)' % s['why'] if s.get('why') else ''),
                           context={'stl': _basename(s.get('path')), 'stamp': state, 'geom': s.get('geom_hash')}))
    return out


@judge
def project_stl_without_object(facts, params, ctx):
    """Rule project.stl_without_object. Reads project_consistency facts: "stls"[].objects (names of the objects of any
    registered project that hold this geometry). Empty: FAIL. Not a list: NOT_RUN."""
    stls, out = _rows(facts, 'stls', ctx)
    for s in stls:
        part = _project_part(facts, s, _basename(s.get('path')))
        if not isinstance(s.get('objects'), list):
            out.append(_not_run(ctx, 'STL %s: it was not looked up in the projects' % s.get('path'), part=part))
        elif not s['objects']:
            out.append(finding(ctx['rule'], part, 'FAIL', 'STL %s matches no object of any registered project: a mesh swap would skip it' % s.get('path'),
                               context={'stl': _basename(s.get('path')), 'geom': s.get('geom_hash')}))
    return out


@judge
def project_object_on_no_plate(facts, params, ctx):
    """Rule project.object_on_no_plate. Reads project_consistency facts: "objects"[].plate (None = on no plate): FAIL.
    An object without a "plate" key, or a project file that does not exist: NOT_RUN."""
    objects, out = _objects(facts, ctx)
    for o in objects:
        part = _project_part(facts, o, o.get('name'))
        if 'plate' not in o:
            out.append(_not_run(ctx, 'object %s: its plate was not read' % o.get('name'), part=part))
        elif o['plate'] is None:
            out.append(finding(ctx['rule'], part, 'FAIL', 'object %s of %s is on no plate: it is not sliced and every mesh swap skips it' % (o.get('name'), facts.get('file')),
                               context={'object': o.get('name'), 'plate': None}))
    return out


@judge
def project_object_not_printable(facts, params, ctx):
    """Rule project.object_not_printable. Reads project_consistency facts: "objects"[].printable. False: FAIL.
    Not a bool, or a project file that does not exist: NOT_RUN."""
    objects, out = _objects(facts, ctx)
    for o in objects:
        part = _project_part(facts, o, o.get('name'))
        if not isinstance(o.get('printable'), bool):
            out.append(_not_run(ctx, 'object %s: its printable flag was not read' % o.get('name'), part=part))
        elif not o['printable']:
            out.append(finding(ctx['rule'], part, 'FAIL', 'object %s of %s (plate: %s) is switched off (not printable): no slice checks it' % (o.get('name'), facts.get('file'), o.get('plate') or 'none'),
                               context={'object': o.get('name'), 'printable': False}))
    return out


@judge
def project_mesh_unstamped(facts, params, ctx):
    """Rule project.mesh_unstamped. Reads project_consistency facts: "objects"[].stamp ({"state": "valid" | "stale" |
    "none", "why", "sig12"}). state "none": FAIL. No stamp object or another state, or a project file that does not
    exist: NOT_RUN."""
    objects, out = _objects(facts, ctx)
    for o in objects:
        part, state = _project_part(facts, o, o.get('name')), _stamp_state(o)
        if state == 'none':
            why = o['stamp'].get('why')
            out.append(finding(ctx['rule'], part, 'FAIL', 'the mesh of object %s in %s is the geometry of no stamp%s' % (o.get('name'), facts.get('file'), ' (%s)' % why if why else ''),
                               context={'object': o.get('name'), 'stamp': 'none', 'pose': o.get('pose')}))
        elif state not in ('valid', 'stale'):
            out.append(_not_run(ctx, 'object %s: its stamp was not looked up' % o.get('name'), part=part))
    return out


@judge
def project_mesh_stale(facts, params, ctx):
    """Rule project.mesh_stale. Reads project_consistency facts: "objects"[].stamp.state == "stale" (the mesh is an older
    geometry than the part's current stamped STL, or its stamp is no longer the head): FAIL with "why".
    (An object whose stamp state cannot be read is reported by project_mesh_unstamped.)"""
    objects, out = _objects(facts, ctx)
    for o in objects:
        if _stamp_state(o) != 'stale':
            continue
        stamp = o['stamp']
        out.append(finding(ctx['rule'], _project_part(facts, o, o.get('name')), 'FAIL',
                           'the mesh of object %s in %s is not the current stamped geometry of its part%s' % (o.get('name'), facts.get('file'), ': %s' % stamp['why'] if stamp.get('why') else ''),
                           context={'object': o.get('name'), 'stamp': 'stale', 'sig12': stamp.get('sig12'), 'pose': o.get('pose')}))
    return out


@judge
def project_build_table_differs(facts, params, ctx):
    """Rule project.build_table_differs. Reads project_consistency facts: "build_table" ({"file", "literal", "differs":
    [{"what", "table", "project"}]} or None). literal True and differs not empty: one FAIL per row.
    literal False: one INFO "the build table is computed: not compared". None (the project names no build table):
    nothing. No "build_table" key, or a literal table without a "differs" list: NOT_RUN.
    The finding's part is "<project key>:<file name of the table>"."""
    if not isinstance(facts, dict) or 'build_table' not in facts:
        return [_not_run(ctx, 'the facts do not say whether the project has a build table')]
    bt = facts['build_table']
    if bt is None:
        return []
    if not isinstance(bt, dict):
        return [_not_run(ctx, 'the build table facts cannot be read')]
    part = '%s:%s' % (facts.get('project') or '?', _basename(bt.get('file')))
    if bt.get('literal') is not True:
        return [finding(ctx['rule'], part, 'INFO', 'the build table %s is computed: not compared with the project' % bt.get('file'), context={'literal': False})]
    if not isinstance(bt.get('differs'), list):
        return [_not_run(ctx, 'the build table %s was not compared with the project' % bt.get('file'), part=part)]
    out = []
    for d in bt['differs']:
        d = d if isinstance(d, dict) else {'what': str(d)}
        out.append(finding(ctx['rule'], part, 'FAIL', '%s: the build table %s says %s, the saved project has %s' % (d.get('what'), bt.get('file'), d.get('table'), d.get('project')),
                           context={'what': d.get('what'), 'table': d.get('table'), 'project': d.get('project')}))
    return out


@judge
def project_target_differs(facts, params, ctx):
    """Rule project.target_differs. Reads project_consistency facts: "target_declared", "target_file" and
    "objects"[].stamp.target. The file's nozzle or line width differs from the declaration: FAIL on
    "<project key>:<file name>", measure = the file's value, limit = the declared one. An object whose stamp was judged
    for another nozzle or line width: FAIL on that part. No declared target, a file whose nozzle or line width cannot
    be read, or a project file that does not exist: NOT_RUN. (An object with no stamp target is left to
    project_mesh_unstamped.)"""
    objects, out = _objects(facts, ctx)
    if out and not objects and facts.get('exists') is False:
        return out
    want, have = facts.get('target_declared'), facts.get('target_file')
    keys = (('nozzle', 'nozzle'), ('line_width', 'line width'))
    if not isinstance(want, dict) or not all(_num(want.get(k)) for k, _ in keys):
        return out + [_not_run(ctx, 'project %s has no declared target (nozzle and line width) in gate/project.json: nothing to compare with' % facts.get('project'))]
    file_part = '%s:%s' % (facts.get('project') or '?', _basename(facts.get('file')))
    for k, word in keys:
        if not isinstance(have, dict) or not _num(have.get(k)):
            out.append(_not_run(ctx, 'the %s of %s could not be read' % (word, facts.get('file')), part=file_part))
        elif abs(have[k] - want[k]) > 0.005:
            out.append(finding(ctx['rule'], file_part, 'FAIL', '%s has %s %s, its declared target says %s' % (facts.get('file'), word, _g(have[k]), _g(want[k])),
                               measure=have[k], limit=want[k], context={'what': k, 'file': have[k], 'declared': want[k]}))
    for o in objects:
        stamp = o.get('stamp')
        t = stamp.get('target') if isinstance(stamp, dict) else None
        if not isinstance(t, dict):
            continue
        off = [word for k, word in keys if not _num(t.get(k)) or abs(t[k] - want[k]) > 0.005]
        if off:
            out.append(finding(ctx['rule'], _project_part(facts, o, o.get('name')), 'FAIL',
                               'object %s was judged for nozzle %s / line %s, the project %s is declared %s / %s' % (o.get('name'), _g(t.get('nozzle')), _g(t.get('line_width')), facts.get('project'), _g(want['nozzle']), _g(want['line_width'])),
                               measure=t.get('nozzle') if _num(t.get('nozzle')) else None, limit=want['nozzle'],
                               context={'object': o.get('name'), 'judged': [t.get('nozzle'), t.get('line_width')], 'declared': [want['nozzle'], want['line_width']]}))
    return out


def _declared_for(target, name, key):
    """(the value the declared target gives one object for a fit setting, True when the target names that object for
    that key). "objects": {"<object name>": {"layer_height": 0.2}} in a declared target says an override that is
    meant; every other object takes the project's own value."""
    objects = target.get('objects') if isinstance(target, dict) else None
    own = objects.get(name) if isinstance(objects, dict) and isinstance(name, str) else None
    v = _declared(own, key) if isinstance(own, dict) else None
    return (v, True) if v is not None else (_declared(target, key), False)


@judge
def project_settings_match(facts, params, ctx):
    """Rule project.settings_match. Reads project_consistency facts: "target_declared" and "fit_settings" ({"keys",
    "global": {key: value}, "listed": {key: bool}, "objects": [{"name", "part", "overrides", "parts": [{"name",
    "overrides"}]}], "objects_read", "objects_unread", "object_names", "repeated", "layer_members"}). Parameter
    target.fit_settings (the keys, by their names in the project settings).
    What is compared is the value each object PRINTS with: its own override, else the global value of the file. The
    declared target says it for the project, and under "objects" for an object that is meant to differ
    ({"objects": {"<object name>": {"layer_height": 0.2}}}).
    For every key:
    - the global value differs from the declared one: FAIL on "<project key>:<file name>", measure = the file's value,
      limit = the declared one. One case apart: every object of the file overrides the key with its declared value.
      Nothing prints with the global value then: INFO (a new object would);
    - an object, or one part of an object, overrides the key with another value than the target declares for that
      object: FAIL on that object's part;
    - the target declares an override for an object and the object has none (it prints with the global value): FAIL;
    - the target declares an override for an object the file does not hold: FAIL (renamed or removed: the override
      prints nowhere).
    Numbers are equal to 0.0005, texts without regard to case.
    One INFO row names the keys whose value equals the declared one and that are NOT in the project's list of settings
    that differ from the system preset ("listed"): the slicer takes the preset's value for them on load, and the gate
    cannot read a preset.
    NOT_RUN, never a pass: no declared target; a key the declared target does not say (one row names them all); a key
    the probe did not read; a key the file does not hold; overrides that were not read, or an <object> tag the reader
    could not take apart; a fit key written twice with two values on one object; an archive that gives single objects
    their own layer heights (a height-range modifier or a variable layer profile: "layer_members"); a project file that
    does not exist."""
    if not isinstance(facts, dict) or facts.get('exists') is False:
        return [_not_run(ctx, 'the project file %s does not exist: its settings were not compared' % (facts.get('file') if isinstance(facts, dict) else '?'))]
    part, fs, want = _file_part(facts), facts.get('fit_settings'), facts.get('target_declared')
    keys = _fit_keys(pv(params, 'target.fit_settings'))
    if not isinstance(fs, dict):
        return [_not_run(ctx, 'the fit settings of %s were not read' % facts.get('file'), part=part)]
    if not isinstance(want, dict):
        return [_not_run(ctx, 'project %s has no declared target in gate/project.json: nothing says what its fits were measured under (%s)' % (facts.get('project'), ', '.join(keys)), part=part)]
    out = []
    missing = [k for k in keys if _declared(want, k) is None]
    if missing:
        out.append(_not_run(ctx, 'the declared target of project %s does not say %s: the owner adds what the fits were measured under to gate/project.json "targets"'
                            % (facts.get('project'), ', '.join('"%s"' % {'nozzle_diameter': 'nozzle'}.get(k, k) for k in missing)), part=part))
    read, glob = {k for k in _list(fs.get('keys')) if isinstance(k, str)}, fs.get('global')
    objects = [o for o in _list(fs.get('objects')) if isinstance(o, dict)]
    n_read = fs.get('objects_read')
    held = fs.get('object_names') if isinstance(fs.get('object_names'), list) else None
    listed, unlisted = fs.get('listed'), []
    for k in keys:
        w = _declared(want, k)
        if w is None:
            continue
        if k not in read:
            out.append(_not_run(ctx, '%s of %s was not read (the probe looked at: %s)' % (k, facts.get('file'), ', '.join(sorted(read)) or 'nothing'), part=part))
        elif not isinstance(glob, dict) or glob.get(k) is None:
            out.append(_not_run(ctx, '%s holds no global value of %s' % (facts.get('file'), k), part=part))
        elif not _same_setting(glob[k], w):
            # does anything print with the global value? Not when every object overrides the key with its declared value
            own = [o for o in objects if isinstance(o.get('overrides'), dict) and k in o['overrides']
                   and _same_setting(o['overrides'][k], _declared_for(want, o.get('name'), k)[0])]
            if _num(n_read) and n_read > 0 and len(own) == n_read and not fs.get('objects_unread'):
                out.append(finding(ctx['rule'], part, 'INFO', '%s has the global %s = %s, its declared target says %s. Every one of its %d object(s) overrides it with the declared value, so nothing prints with %s today; an object added later would'
                                   % (facts.get('file'), k, _val(glob[k]), _val(w), n_read, _val(glob[k])),
                                   context={'key': k, 'scope': 'global, overridden by every object', 'file': glob[k], 'declared': w}))
            else:
                out.append(finding(ctx['rule'], part, 'FAIL', '%s has %s = %s, its declared target says %s: the fits of this project were measured under %s'
                                   % (facts.get('file'), k, _val(glob[k]), _val(w), _val(w)),
                                   measure=glob[k] if _num(glob[k]) else None, limit=w if _num(w) else None,
                                   context={'key': k, 'scope': 'global', 'file': glob[k], 'declared': w}))
        elif isinstance(listed, dict) and listed.get(k) is False:
            unlisted.append(k)
    if unlisted:
        out.append(finding(ctx['rule'], part, 'INFO', '%s: %s equal the declared target and are not in the project\'s list of settings that differ from the system preset '
                                                      '("different_settings_to_system"). The slicer takes the value of the preset for them when it loads the file; the gate '
                                                      'cannot read a preset. Where the preset has another value, set the key with bambu3mf.set_project: it lists it'
                           % (facts.get('file'), ', '.join(unlisted)), context={'unlisted': sorted(unlisted)}))
    if 'layer_height' in keys and _list(fs.get('layer_members')):
        out.append(_not_run(ctx, '%s gives single objects their own layer heights (%s): a height-range modifier or a variable layer profile. Those heights are not read, so the layer height was not compared for them'
                            % (facts.get('file'), ', '.join(str(m) for m in _list(fs.get('layer_members')))), part=part))
    for r in _list(fs.get('repeated')):
        if isinstance(r, dict) and r.get('key') in keys:
            out.append(_not_run(ctx, '%s of %s sets %s twice (%s): which one the slicer takes is not known' % (r.get('scope'), facts.get('file'), r.get('key'), ' and '.join(_val(v) for v in _list(r.get('values')))), part=part))
    if n_read is None:
        return out + [_not_run(ctx, 'the per-object settings of %s were not read: an override of a fit setting would not be seen' % facts.get('file'), part=part)]
    if fs.get('objects_unread'):
        out.append(_not_run(ctx, '%s object(s) of %s could not be read from its per-object settings: an override of a fit setting on them would not be seen' % (_g(fs.get('objects_unread')), facts.get('file')), part=part))
    said = want.get('objects') if isinstance(want.get('objects'), dict) else {}
    for o in objects:
        name = o.get('name')
        opart = o.get('part') or '%s:%s' % (facts.get('project') or '?', name)
        over = o.get('overrides') if isinstance(o.get('overrides'), dict) else {}
        scopes = [('object %s' % name, over)]
        scopes += [('part %s of object %s' % (p.get('name'), name), p.get('overrides')) for p in _list(o.get('parts')) if isinstance(p, dict)]
        for scope, got in scopes:
            for k in keys:
                w, _own = _declared_for(want, name, k)
                if w is None or not isinstance(got, dict) or k not in got or _same_setting(got[k], w):
                    continue
                out.append(finding(ctx['rule'], opart, 'FAIL',
                                   '%s of %s overrides %s = %s, the declared target says %s%s' % (scope, facts.get('file'), k, _val(got[k]), _val(w), ' for this object' if _own else ''),
                                   measure=got[k] if _num(got[k]) else None, limit=w if _num(w) else None,
                                   context={'key': k, 'scope': scope, 'file': got[k], 'declared': w}))
    for name in sorted(said, key=str):                # an override the target declares and the file does not hold
        own = said[name] if isinstance(said[name], dict) else {}
        for k in keys:
            w = _declared(own, k)
            if w is None:
                continue
            if held is not None and name not in held:
                out.append(finding(ctx['rule'], '%s:%s' % (facts.get('project') or '?', name), 'FAIL',
                                   'the declared target says %s = %s for the object %s, and %s holds no object of that name: renamed or removed, so the override prints nowhere'
                                   % (k, _val(w), name, facts.get('file')), limit=w if _num(w) else None,
                                   context={'key': k, 'scope': 'object %s' % name, 'file': None, 'declared': w, 'object': 'missing'}))
            elif not any(o.get('name') == name and isinstance(o.get('overrides'), dict) and k in o['overrides'] for o in objects):
                g = glob.get(k) if isinstance(glob, dict) else None
                if g is None or _same_setting(g, w):
                    continue                           # it prints with the global value, and that is the declared one
                out.append(finding(ctx['rule'], next((o.get('part') for o in objects if o.get('name') == name and o.get('part')), None) or '%s:%s' % (facts.get('project') or '?', name), 'FAIL',
                                   'the declared target says %s = %s for the object %s, but in %s the object has no override: it prints with the global %s'
                                   % (k, _val(w), name, facts.get('file'), _val(g)), measure=g if _num(g) else None, limit=w if _num(w) else None,
                                   context={'key': k, 'scope': 'object %s' % name, 'file': g, 'declared': w, 'object': 'no override'}))
    return out


@judge
def project_settings_changed_by_write(facts, params, ctx):
    """Rule project.settings_changed_by_write. Reads "settings_write": what the guard found when it compared the archive
    about to be written with the file on disk (probes.project_consistency.settings_diff): {"keys", "compared": bool,
    "why", "changed": [{"key", "scope", "before", "after", "asked": bool, "by"}]}, or None when no write is under way
    (gate.py verify): then there is nothing to judge. Parameter target.fit_settings.
    A changed setting the caller did not ask for: FAIL on "<project key>:<file name>", measure = the new value,
    limit = the value on disk. A change the caller asked for: INFO. A row with "note" (the key leaves the project's
    list of settings that differ from the system preset while its value stays, so the slicer would load the preset's
    value): FAIL. A row with "unlisted" (the value changes and the key is not in that list afterwards) is judged like
    any other; its text says that the slicer may load the preset's value. NOT_RUN: the facts do not say whether a write is
    under way; the two could not be compared; a key of today's list was not compared."""
    if not isinstance(facts, dict) or 'settings_write' not in facts:
        return [_not_run(ctx, 'the facts do not say whether a write is under way')]
    w = facts['settings_write']
    if w is None:
        return []
    part = _file_part(facts)
    if not isinstance(w, dict) or w.get('compared') is not True or not isinstance(w.get('changed'), list):
        why = w.get('why') if isinstance(w, dict) else None
        return [_not_run(ctx, 'the fit settings of %s on disk and of the archive about to be written were not compared%s' % (facts.get('file'), ' (%s)' % why if why else ''), part=part)]
    out = []
    skipped = [k for k in _fit_keys(pv(params, 'target.fit_settings')) if k not in _list(w.get('keys'))]
    if skipped:
        out.append(_not_run(ctx, 'this write of %s was not compared for %s' % (facts.get('file'), ', '.join(skipped)), part=part))
    for r in w['changed']:
        if not isinstance(r, dict):
            out.append(_not_run(ctx, 'a row of the settings comparison cannot be read', part=part))
            continue
        context = {'key': r.get('key'), 'scope': r.get('scope'), 'before': r.get('before'), 'after': r.get('after')}
        unlisted = ('. The key is not in the project\'s list of settings that differ from the system preset: where the preset has another value the slicer '
                    'loads that one (bambu3mf.set_project lists the key)') if r.get('unlisted') is True else ''
        if isinstance(r.get('note'), str) and r['note'].strip():
            out.append(finding(ctx['rule'], part, 'FAIL', 'this write of %s %s' % (facts.get('file'), r['note'].strip()), context=dict(context, note='override list')))
        elif r.get('asked') is True:
            out.append(finding(ctx['rule'], part, 'INFO', '%s (%s) of %s changes from %s to %s, as asked by %s%s' % (r.get('key'), r.get('scope'), facts.get('file'), _val(r.get('before')), _val(r.get('after')), r.get('by') or 'the caller', unlisted),
                               context=context))
        else:
            out.append(finding(ctx['rule'], part, 'FAIL', 'this write changes %s (%s) of %s from %s to %s, and the caller did not ask for it%s' % (r.get('key'), r.get('scope'), facts.get('file'), _val(r.get('before')), _val(r.get('after')), unlisted),
                               measure=r.get('after') if _num(r.get('after')) else None, limit=r.get('before') if _num(r.get('before')) else None, context=context))
    return out


# ---- regression (facts built by gate_plan from fusion_scan and fusion_census) --------------------------------------------------------------

@judge
def regression_unintended(facts, params, ctx):
    """Rule regression.unintended. Reads {"changed": [{"part", "sig", "prev_sig"}]} (parts of the document whose signature
    differs from their last checked one) and ctx["intent"]. intent None: nothing. A changed part that is not in the
    intent: FAIL on that part, context = the two signatures. A row with no prev_sig (a part that was never checked) has
    nothing it changed from: nothing."""
    intent = ctx.get('intent')
    if intent is None:
        return []
    named = set([intent] if isinstance(intent, str) else intent)
    changed, out = _rows(facts, 'changed', ctx)
    for c in changed:
        part = c.get('part')
        if not isinstance(part, str) or not part:
            out.append(_not_run(ctx, 'a changed part has no id'))
        elif part not in named and c.get('prev_sig'):
            out.append(finding(ctx['rule'], part, 'FAIL', 'part %s changed (signature %s -> %s) and this run did not name it' % (part, str(c.get('prev_sig'))[:12], str(c.get('sig'))[:12]),
                               context={'sig': c.get('sig'), 'prev_sig': c.get('prev_sig')}))
    return out


def _same_segments(a, b, tol=gate_features.GRID):
    """Do two holes have the same geometry: the same number of segments, and every diameter, far diameter of a cone and
    length within `tol` mm of each other (the step a context rounds its numbers to)? Segments that cannot be read are
    never the same."""
    if not a or not b or len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if _num(x.get('d2')) != _num(y.get('d2')):
            return False
        for k in ('d', 'len') + (('d2',) if _num(x.get('d2')) else ()):
            if abs(x[k] - y[k]) > tol + _EPS:
                return False
    return True


def _same_ends(a, b, tol=gate_features.GRID):
    """Do two census holes end the same way: both through or both blind, and, where both hold the material left under
    the hole ("floor"), the same within `tol`? A blind hole that goes through now, and a floor that got thinner, are
    changes of the part whatever the segments say."""
    if bool(a.get('through')) != bool(b.get('through')):
        return False
    fa, fb = a.get('floor'), b.get('floor')
    return not (_num(fa) and _num(fb)) or abs(fa - fb) <= tol + _EPS


def _class_word(hole, kind):
    """What a census hole is called in a row about its class: 'an insert pocket (M3x4x5)', 'a plain hole (M3 clearance)',
    'a plain hole (no table row)'."""
    c = hole.get('class') if isinstance(hole.get('class'), dict) else {}
    if kind == 'insert_pocket':
        return 'an insert pocket%s' % (' (%s)' % c['kind'] if c.get('kind') else '')
    what = ' '.join(str(c[k]).replace('_', ' ') for k in ('kind', 'role') if c.get(k))
    return 'a plain hole (%s)' % (what or 'no table row')


@judge
def regression_feature_lost(facts, params, ctx):
    """Rule regression.feature_lost. Reads fusion_census facts of the part and ctx["prev"] (the census of its last checked
    signature; None = nothing to compare, no findings). Every hole or opening of prev whose anchor matches none of
    today's (gate_features.find): FAIL with ask = "regression.intended", anchor = the lost feature's anchor, context =
    its rounded segments (an opening: shape and size). A hole that was through and is blind now is lost too.

    The kind is part of an anchor (insert pocket or plain hole), and the kind comes from the class the tools gave the
    hole. So a hole that is still at its place under the other kind is looked at again:
      - its segments changed (_same_segments: any diameter or length off by more than 0.05), or it ends another way
        (_same_ends: through became blind or blind became through, or the material left under it moved by more than
        0.05): the feature is lost, FAIL as above ("a hole is still at that place, it is no longer ...");
      - its segments and its ends are the same: the geometry did not change, only its class did (a corrected table row, a newer
        classifier, another ref on its axis). Nothing is lost. ONE INFO row for the part counts these holes and names
        the first of them, because the class decides which rules judge a hole and which anchor a waiver or an answer
        hangs on: rows that are new on an unchanged part, and a waiver that no longer finds its hole, have this cause.
    Today's census without its "holes" or "openings" list: NOT_RUN, and nothing is called lost."""
    prev = ctx.get('prev')
    if prev is None:
        return []
    holes, out = _rows(facts, 'holes', ctx)
    openings, more = _rows(facts, 'openings', ctx)
    if out or more:
        return out + more
    if not isinstance(prev, dict):
        return [_not_run(ctx, 'the census of the last checked geometry cannot be read')]
    holes = [dict(h, anchor=_anchor(h.get('anchor'))) for h in holes]
    openings = [dict(o, anchor=_anchor(o.get('anchor'))) for o in openings]
    reclassed = []
    for old in _list(prev.get('holes')):
        a = _anchor(old.get('anchor')) if isinstance(old, dict) else None
        if a is None:
            continue
        segs, now = _segments(old), gate_features.find(a, holes)
        what = 'insert pocket' if a['kind'] == 'insert_pocket' else 'hole'
        if now is None:
            other = gate_features.find(dict(a, kind='hole' if a['kind'] == 'insert_pocket' else 'insert_pocket'), holes)
            if other is not None and _same_segments(segs, _segments(other)) and _same_ends(old, other):
                reclassed.append((a, old, other))    # the same hole under another class: nothing changed in the part
                continue
            text = '%s %s at %s is gone' % (what, _shape(segs), _at(a))
            if other is not None and old.get('through') and not other.get('through'):
                text = '%s %s at %s went through the part and is blind now (%s)' % (what, _shape(segs), _at(a), _shape(_segments(other)))
            elif other is not None and other.get('through') and not old.get('through'):
                text = '%s %s at %s was blind and goes through the part now (%s), and it is classed differently' % (what, _shape(segs), _at(a), _shape(_segments(other)))
            elif other is not None and _same_segments(segs, _segments(other)):
                text = ('%s %s at %s is classed differently now and the material under it changed: %s mm were left under it, now %s'
                        % (what, _shape(segs), _at(a), _g(old.get('floor')), _g(other.get('floor'))))
            elif other is not None:
                text += ': a hole is still at that place (%s), it is no longer %s' % (_shape(_segments(other)), 'an insert pocket' if what == 'insert pocket' else 'a plain hole')
        elif old.get('through') and not now.get('through'):
            text = '%s %s at %s went through the part and is blind now (%s)' % (what, _shape(segs), _at(a), _shape(_segments(now)))
        else:
            continue
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', text, anchor=a, ask='regression.intended',
                           context={'lost': a['kind'], 'segments': [[s['d'], s['len']] for s in segs or []], 'through': bool(old.get('through'))}))
    for old in _list(prev.get('openings')):
        a = _anchor(old.get('anchor')) if isinstance(old, dict) else None
        if a is None or gate_features.find(a, openings) is not None:
            continue
        size = old.get('size') if isinstance(old.get('size'), list) else []
        out.append(finding(ctx['rule'], ctx['part'], 'FAIL', '%s %s at %s is gone' % (old.get('shape') or 'opening', ' x '.join(_g(c) for c in size), _at(a)),
                           anchor=a, ask='regression.intended', context={'lost': old.get('shape'), 'size': size, 'through': bool(old.get('through'))}))
    if reclassed:
        a, old, other = reclassed[0]
        n = len(reclassed)
        out.append(finding(ctx['rule'], ctx['part'], 'INFO',
                           '%d hole%s of the last checked geometry %s classed differently now and did not change (the same segments within %s mm): hole %s at %s was %s and is %s%s. '
                           'Nothing is lost. The class comes from the tables and the tools, and it decides which rules judge a hole: rows that are new on an unchanged part, '
                           'and a waiver or an answer that no longer finds its hole, have this cause'
                           % (n, '' if n == 1 else 's', 'is' if n == 1 else 'are', _g(gate_features.GRID), _shape(_segments(old)), _at(a),
                              _class_word(old, a['kind']), _class_word(other, other['anchor']['kind']), '' if n == 1 else '; and %d more' % (n - 1)),
                           context={'reclassed': sorted([gate_features.anchor_key(x[0]), x[2]['anchor']['kind']] for x in reclassed)}))
    return out
