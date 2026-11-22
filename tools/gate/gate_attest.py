"""Attest: the rules no script can check, asked as questions and answered with evidence.

Where questions come from:
  - a rule of level "attest" with "per": "part": one question per part at each new signature (design-stage rules after
    `gate.py run`, export-stage rules at `gate.py export`), when the rule applies to the part and is not deferred;
  - a finding that carries "ask": <attest rule id>: one question for that feature. An answer closes the finding.

A question:  {"q": "Q-51b0c2", "rule", "part", "anchor", "context", "question": text with {feature} and {by} filled in,
              "evidence": [kinds the rule allows], "labels": [the fixed list, when "label" is allowed],
              "finding": finding id or None, "answer": the answer or None}
  the id is gate_features.feature_id("Q", rule, part, anchor, context); for a part-level question the anchor is the part
  anchor and the context is the first 12 characters of the signature (asked again for every new geometry); for a finding
  it is the finding's anchor and context (the answer is carried to the next signature while that feature is unchanged).
  A question also says "stage" (of its rule) and, for a finding, "finding_rule" (the rule that raised it).

An answer (gate/attest/<doc>/<part>.json = {"part": id, "answers": [...]}):
  {"q", "rule", "part", "anchor", "context", "kind": "label" | "shot" | "param" | "na" | "waiver", "value": ...,
   "sig": signature at the time, "time", "run"}

Evidence kinds. No kind takes a free number typed by the agent.
  label   value = one text out of the rule's attest.labels, exactly. A label the facts of the part contradict is
          refused (the rule's attest.held: "this part has no screws" while the screws probe lists two)
  shot    value = path of a picture that exists and is newer than the start of the part's latest run
  param   value = name of a parameter whose value in this project has tag M or U. Never a value of the declared
          target. A rule can narrow it (attest.param): fits.pocket_content takes only a parameter of class "fit" whose
          value is the diameter of that hole
  na      value = the reason the question does not apply (at least three words that say something)
  waiver  value = id of a valid waiver in gate/owner/waivers.json for this rule, part and anchor
A stamp that rests on n answers has the verdict "pass_attested" with "attested": n; the report lists them for the owner.

  facts   not typed by anybody: the gate gives it itself when the facts of the part PROVE that a question cannot
          apply (the rule's attest.auto: "this part has no screws" while the screw unit lists none, the census holds no
          hole a screw of the table could use, and nothing bought sits on the axis of a hole). value = the label, or
          the reason of a "not applicable"; "as" = "label" | "na"; "proof" = what the facts say. Such an answer does not
          count as attested (it is no statement of the agent), the report lists it as "answered by facts", and it holds
          only while the proof holds: fact_answer() is asked again at every reading.

Questions of an inherited part. A part whose present geometry is the one an audit judged (gate_report.is_inherited)
is not asked by `run`, `status` and `attest list`: nothing about it changed. Its questions, the part-level ones and the
ones a finding carries, are listed as waiting, and they are asked when the part changes (a new signature) and at its
export (the stamp rules stay: no stamp without them). `attest list --part ID` shows them, and `attest answer` takes an
answer to one.

The waiver kind is not typed by the agent: `gate.py owner waive Q-...` writes the owner's waiver for the question (its
rule, part, anchor and context) and stores the answer {"kind": "waiver", "value": "W-<n>"} with it. Such an answer
counts only while that waiver is still in gate/owner/waivers.json and sound. A finding with "ask" can also be waived as
a finding (`gate.py owner waive F-...`): then gate_features.apply_waivers closes it and no answer is needed."""
import os, re
import gate_common, gate_features, gate_registry
from gate_common import GateError, EXIT_PASS, EXIT_ATTEST, read_json, write_json

KINDS = ('label', 'shot', 'param', 'na', 'waiver')         # what `attest answer` and `owner waive` can give
FACTS = 'facts'                                             # the kind of an answer the gate gives itself (fact_answer)
# the name patterns of the kinds of standard parts. A pattern alone makes no body a standard part: _standard_part()
# also wants the designation of the part in its own name (a thread of the tables, a DIN or ISO number, a size)
FASTENER_PATTERNS = ('screws.ref_pattern', 'washers.ref_pattern', 'inserts.ref_pattern', 'screws.nut_ref_pattern', 'screws.wood_pattern')
# the parameters the fact tests and the held labels read
FACT_PARAMS = ('motors.ref_pattern', 'bearings.ref_pattern', 'screws.any_hole_d_max', 'fits.pass_room_min') + FASTENER_PATTERNS
PICTURES = ('.png', '.jpg', '.jpeg', '.webp', '.gif')


def question_id(rule, part, anchor, context):
    """'Q-51b0c2': gate_features.feature_id('Q', ...)."""
    return gate_features.feature_id('Q', rule, part, anchor, context)


def load_answers(root, part):
    """The "answers" list of gate/attest/<doc>/<part>.json; [] when there is none."""
    data = read_json(gate_common.attest_path(root, part), {})
    return list(data.get('answers') or []) if isinstance(data, dict) else []


def find_answer(answers, rule, anchor, context):
    """The newest answer for that rule whose anchor matches (gate_features.anchors_match) and whose context is equal,
    or None."""
    best = None
    for a in answers or []:
        if a.get('rule') != rule or a.get('context') != context or not gate_features.anchors_match(anchor, a.get('anchor')):
            continue
        if best is None or (a.get('time') or '') >= (best.get('time') or ''):
            best = a
    return best


def _answer_live(a, waivers):
    """Is a stored answer still worth something? Every kind but "waiver": yes, as far as this function can say (an
    answer of kind "facts" is kept in step with the facts by record_fact_answers() at every judging; a label or a "not
    applicable" that the facts contradict today is taken out by live_answers() when it is given the registry). A
    waiver answer: only while the waiver it names is in gate/owner/waivers.json, is sound, and is for this part, anchor
    and context."""
    if not isinstance(a, dict):
        return False
    if a.get('kind') != 'waiver':
        return True
    w = next((x for x in waivers if isinstance(x, dict) and x.get('id') == a.get('value')), None)
    return (w is not None and w.get('part') == a.get('part') and w.get('context') == a.get('context')
            and gate_features.anchors_match(a.get('anchor'), w.get('anchor')) and not gate_features.validate_waiver(w))


def live_answers(root, part, reg=None):
    """load_answers() without the answers whose waiver was withdrawn: what a stamp may rest on. With `reg` (the
    registry) also without the answers to a question about the whole part that the facts of the part contradict today
    (stored_contradicted): "this part has no screws" given before the screw unit found two."""
    waivers = _owner_waivers(root)
    out = [a for a in load_answers(root, part) if _answer_live(a, waivers)]
    if reg is not None:
        out = [a for a in out if not ((a.get('anchor') or {}).get('kind') == 'part' and a.get('sig')
                                      and stored_contradicted(root, reg, a.get('rule'), part, a['sig'], a.get('kind'), a.get('value')))]
        out = [a for a in out if a.get('kind') != FACTS or _facts_still(root, reg, part, a)]
    return out


def _facts_still(root, reg, part, a):
    """Do the facts still give the stored answer `a` of kind "facts" (fact_answer: the same value)? The test status and
    export make (questions_for_part), so that a stamp never rests on an answer they show as an open question."""
    rule = (reg.get('rules') or {}).get(a.get('rule'))
    if not isinstance(rule, dict) or not a.get('sig'):
        return False
    got = fact_answer(root, reg, rule, part, a['sig'])
    return isinstance(got, dict) and got.get('value') == a.get('value') and got.get('as', a.get('as')) == a.get('as', got.get('as'))


def answers_gone(root, part, qids, reg=None):
    """The ids out of `qids` (the "answers" of a stamp) that no live answer of the part holds any more: the answer was
    removed from gate/attest, or it was a waiver and that waiver is gone, or (with `reg`) the facts of the part
    contradict it today. [] when every one still stands."""
    have = {a.get('q') for a in live_answers(root, part, reg)}
    return [q for q in qids or [] if q not in have]


def closes(finding, answers):
    """Does an answer close this finding? True when the finding has "ask" and find_answer(answers, finding["ask"],
    finding["anchor"], finding["context"]) is not None."""
    return bool(finding.get('ask')) and find_answer(answers, finding['ask'], finding.get('anchor'), finding.get('context')) is not None


def _owner_waivers(root):
    data = read_json(gate_common.gate_path(root, 'owner', 'waivers.json'), {})
    return [w for w in (data.get('waivers') or [])] if isinstance(data, dict) else []


def _waiver_for(waivers, rules, part, anchor, context):
    """The sound waiver of the owner for one of these rules on this part, anchor and context, or None."""
    for w in waivers:
        if not isinstance(w, dict) or w.get('part') != part or w.get('rule') not in rules or w.get('context') != context:
            continue
        if gate_features.anchors_match(anchor, w.get('anchor')) and not gate_features.validate_waiver(w):
            return w
    return None


def _waiver_holds(waivers, answer_, question):
    """Is the waiver a stored answer of kind "waiver" names still there, and still a sound waiver for this question?"""
    w = next((x for x in waivers if isinstance(x, dict) and x.get('id') == answer_.get('value')), None)
    return w is not None and _waiver_for([w], {question['rule'], question.get('finding_rule')} - {None}, question['part'],
                                         question['anchor'], question['context']) is not None


# ---- answered by facts ---------------------------------------------------------------------------------------------------
#
# A fact test says whether the facts of one part PROVE that something is absent. It returns (state, text):
#   True   proven absent: every list it reads was measured and is empty
#   False  the facts show it (text says what and how many)
#   None   it cannot say: a unit it needs has no usable facts for this geometry (text says which)
# Only True answers a question. "No screw recognised" alone is never True: a hole a screw could use, or a bought body on
# the axis of a hole, makes it False, because the ref of that screw may be missing or not recognised.

def _n(x):
    """How many rows a fact holds: the length of a list, the number itself when a probe stores a count."""
    return len(x) if isinstance(x, (list, tuple, dict)) else int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def _rows(facts, key):
    return [r for r in (facts.get(key) or []) if isinstance(r, dict)] if isinstance(facts.get(key), list) else []


def _leaf(name):
    """The body's own name in the name of a ref: "<component> / <body>" gives "<body>", a one-body component its name."""
    return str(name or '').rsplit(' / ', 1)[-1]


def _matches(pattern, text):
    try:
        return bool(pattern) and re.search(pattern, text or '') is not None
    except re.error:
        return False


def _narrowest(hole):
    ds = [seg.get('d') for seg in hole.get('segments') or [] if isinstance(seg, dict) and isinstance(seg.get('d'), (int, float))]
    return min(ds) if ds else None


def _screw_d_max(tables):
    """The widest hole a screw of the screws table passes through (its loosest clearance fit), or None with no table:
    then every hole counts as one a screw could use."""
    best = None
    for row in ((tables or {}).get('screws') or {}).get('sizes', {}).values():
        if not isinstance(row, dict):
            continue
        for d in [row.get('clearance_d')] + list((row.get('clearance_fits') or {}).values()):
            if isinstance(d, (int, float)) and (best is None or d > best):
                best = d
    return best


def _coaxial(holes, kinds=('bought', 'hardware')):
    return [x for h in holes for x in (h.get('coaxial') or []) if isinstance(x, dict) and x.get('kind') in kinds]


def _some(names, n=3):
    names = sorted({str(x) for x in names})
    return ', '.join(names[:n]) + (' and %d more' % (len(names) - n) if len(names) > n else '')


def _hole_d_max(params, tables):
    """The widest hole that is taken for a hole a screw could use: the loosest clearance hole of the screws table, or
    the parameter screws.any_hole_d_max when that is more (a screw of a thread the table has no row for). None with
    neither: then every hole counts."""
    dmax, more = _screw_d_max(tables), params.get('screws.any_hole_d_max')
    if isinstance(more, (int, float)) and not isinstance(more, bool) and (dmax is None or more > dmax):
        return more
    return dmax


def _no_screw_feature(get, params, tables):
    """Reads fusion_screws: "screws", "washers", "unreadable_washers", "passing", "n_screws", "rejected",
    "fastener_holes". fusion_census: "holes"[] "class"."table" (screws, inserts), "segments"[]."d", "coaxial"[]."kind";
    "openings"[] "shape" and "size"; "partial"[]."d". Parameter screws.any_hole_d_max."""
    s, c = get('fusion_screws'), get('fusion_census')
    if s is None or c is None:
        return None, 'the %s has no facts for this geometry' % ('screw unit' if s is None else 'hole census')
    dmax = _hole_d_max(params, tables)
    small = lambda d: isinstance(d, (int, float)) and (dmax is None or d <= dmax + 1e-9)        # noqa: E731
    holes = _rows(c, 'holes')
    found = []
    for key, word in (('screws', 'screw'), ('washers', 'washer'), ('unreadable_washers', 'washer that could not be read'),
                      ('passing', 'screw that passes this part')):
        if _n(s.get(key)):
            found.append('%d %s(s) at this part' % (_n(s.get(key)), word))
    seen = s.get('n_screws')
    if isinstance(seen, (int, float)) and not isinstance(seen, bool) and seen > _n(s.get('screws')) + _n(s.get('passing')):
        found.append('%d screw(s) near the part that are no row of its screws' % (seen - _n(s.get('screws')) - _n(s.get('passing'))))
    if _n(s.get('rejected')):
        found.append('%d body(ies) near the part that a hardware name picked and the shape refused (%s): it is not known what they are'
                     % (_n(s.get('rejected')), _some(_leaf(r.get('name')) for r in _rows(s, 'rejected'))))
    if _n(s.get('fastener_holes')):
        found.append('%d screw hole(s) or insert pocket(s) the screw unit lists' % _n(s.get('fastener_holes')))
    classed = [h for h in holes if isinstance(h.get('class'), dict) and h['class'].get('table') in ('screws', 'inserts')]
    if classed:
        found.append('%d screw hole(s) or insert pocket(s) in the census' % len(classed))
    # a hole that is only NEAR a bearing seat size (class match "band") is a guess by its diameter: a screw could use it as well
    seat = lambda h: isinstance(h.get('class'), dict) and h['class'].get('table') == 'bearings' and h['class'].get('match') == 'exact'     # noqa: E731
    plain = [h for h in holes if h not in classed and not seat(h) and small(_narrowest(h))]
    if plain:
        found.append('%d hole(s) of no table that a screw could use (D%s)' % (len(plain), ', D'.join(sorted({'%g' % _narrowest(h) for h in plain})[:4])))
    slots = [o for o in _rows(c, 'openings') if o.get('shape') == 'slot' and small(min([x for x in o.get('size') or [] if isinstance(x, (int, float))] or [0]))]
    if slots:
        found.append('%d slot(s) a screw could run in' % len(slots))
    arcs = [p for p in _rows(c, 'partial') if small(p.get('d'))]
    if arcs:
        found.append('%d open arc(s) of a hole a screw could use' % len(arcs))
    on_axis = _coaxial(holes)
    if on_axis:
        found.append('%d bought or hardware body(ies) on the axis of a hole (%s)' % (len(on_axis), _some(_leaf(x.get('name')) for x in on_axis)))
    if found:
        return False, '; '.join(found)
    return True, ('the screw unit lists no screw and no washer at or near this part; the census holds no screw hole, no insert pocket, no slot and no '
                  'hole or open arc %s, and nothing bought on the axis of a hole' % ('up to D%g' % dmax if dmax is not None else 'at all'))


def _screws_shown(get, params, tables):
    """What the facts SHOW of screws at this part, as text, or None: rows of the screw unit ("screws", "washers",
    "passing", "unreadable_washers"), a screw hole or insert pocket with a screw or a bought body on its axis
    ("fastener_holes"; facts of an older unit: a census hole of the screws or inserts table with such a body). A hole a
    screw COULD use is not in this list: it makes the question asked, it does not make an answer false."""
    s, c = get('fusion_screws') or {}, get('fusion_census') or {}
    found = []
    for key, word in (('screws', 'screw'), ('washers', 'washer'), ('unreadable_washers', 'washer that could not be read'),
                      ('passing', 'screw that passes this part')):
        if _n(s.get(key)):
            found.append('%d %s(s) at this part' % (_n(s.get(key)), word))
    if isinstance(s.get('fastener_holes'), list):
        held = [r for r in _rows(s, 'fastener_holes') if r.get('screw') or r.get('bodies')]
    else:
        held = [h for h in _rows(c, 'holes') if isinstance(h.get('class'), dict) and h['class'].get('table') in ('screws', 'inserts') and _coaxial([h])]
    if held:
        found.append('%d screw hole(s) or insert pocket(s) with a screw or a bought body on the axis' % len(held))
    return '; '.join(found) or None


def _bearing_names(tables):
    t = (tables or {}).get('bearings') or {}
    return sorted({str(k) for key in ('sizes', 'aliases') for k in (t.get(key) or {})}, key=lambda k: (-len(k), k))


def _bearing_name(name, params, names):
    """Does a name speak of a bearing: bearings.ref_pattern, or a designation of the bearings table as a word of its
    own ("608 (1)", "clamp of the 6204", "shaft: pin + 2 x 608"). The whole name is read, the component in front of
    " / " too: a name can only make the question asked, so the wider reading is the safe one."""
    text = str(name or '')
    if _matches(params.get('bearings.ref_pattern') or '(?i)bearing', text):
        return True
    return bool(names) and _designations(tuple(names)).search(text) is not None


_DESIGNATIONS = {}


def _designations(names):
    """One compiled pattern for "one of these designations as a word of its own": the same answer as one search per
    designation (the engine tries every alternative at every place), read once per table instead of per name."""
    if names not in _DESIGNATIONS:
        _DESIGNATIONS[names] = re.compile(r'(?<![0-9A-Za-z.])(?:%s)(?![0-9A-Za-z])' % '|'.join(re.escape(k) for k in names))
    return _DESIGNATIONS[names]


def _unknown_near(near):
    """The bodies of the fusion_near facts whose kind is "bought": no hardware word in the name says what they are, and
    no unit has read their shape. Such a body can be anything, a bearing or a motor too."""
    return [x for x in _rows(near, 'bodies') if x.get('kind') == 'bought']


def _no_bearing_here(get, params, tables):
    """Reads fusion_bearings: "bearings", "seats_without_bearing", "near_bearings", "rejected", "not_bearings".
    fusion_census: "holes"[] and "bores"[] "class"."role" (bearing_seat) and "class"."table" (bearings), "coaxial"[]
    "kind" and "name". fusion_near: "bodies"[] "name", "path", "kind".
    A name can only make this test False. It is True when the three units have facts, none of them lists a bearing, a
    seat or a ring that was refused, no name on an axis or near speaks of a bearing, and no bought body of unknown
    identity is near (a ring under a name nobody knows is read by no unit)."""
    b, c, near = get('fusion_bearings'), get('fusion_census'), get('fusion_near')
    if b is None or c is None:
        return None, 'the %s has no facts for this geometry' % ('bearing unit' if b is None else 'hole census')
    found, names = [], _bearing_names(tables)
    if _n(b.get('bearings')):
        found.append('%d bearing(s) at this part' % _n(b.get('bearings')))
    if _n(b.get('seats_without_bearing')):
        found.append('%d bearing seat(s) with no bearing in it' % _n(b.get('seats_without_bearing')))
    if _n(b.get('near_bearings')):
        found.append('%d bearing(s) near that another part holds' % _n(b.get('near_bearings')))
    refused = _n(b.get('rejected')) or _n(b.get('not_bearings'))
    if refused:
        found.append('%d body(ies) near that a bearing name picked and the shape refused' % refused)
    holes = _rows(c, 'holes') + _rows(c, 'bores')
    seats = [h for h in holes if isinstance(h.get('class'), dict) and (h['class'].get('role') == 'bearing_seat' or h['class'].get('table') == 'bearings')]
    if seats:
        found.append('%d hole(s) of bearing seat size in the census' % len(seats))
    bodies = _rows(near or {}, 'bodies')
    named = [x.get('name') for x in _coaxial(holes) if _bearing_name(x.get('name'), params, names)]
    named += [x.get('name') for x in bodies if _bearing_name('%s %s' % (x.get('path') or '', x.get('name') or ''), params, names)]
    if named:
        found.append('%d body(ies) on an axis or near whose name, or whose component, speaks of a bearing (%s)' % (len(named), _some(named)))
    unknown = [x for x in _unknown_near(near or {}) if x.get('name') not in named]
    if unknown:
        found.append('%d bought body(ies) within %g mm whose name does not say what they are (%s): no unit read whether one is a bearing'
                     % (len(unknown), near.get('margin') or 0, _some(x.get('name') for x in unknown)))
    if found:
        return False, '; '.join(found)
    if near is None:                                 # nothing shows a bearing, and nobody looked at what is near: no proof
        return None, 'the unit fusion_near has no facts for this geometry: what stands near the part was not read'
    return True, ('the bearing unit lists no bearing, no seat and no refused ring at or near this part; the census holds no hole of bearing seat size; '
                  'no body on an axis or within %g mm is named as a bearing, and every body that near is a fastener by its name' % (near.get('margin') or 0))


def _bearing_shown(get, params, tables):
    """What the facts SHOW of a bearing clamp at this part, or None: clamp rows of its bearings."""
    n = sum(len(r.get('clamps')) for r in _rows(get('fusion_bearings') or {}, 'bearings') if isinstance(r.get('clamps'), list))
    return '%d clamp(s) on the bearings of this part' % n if n else None


def _no_bought_near(get, params, tables):
    """Reads fusion_near: "bodies"[] "kind", "name", "margin". fusion_census: "holes"[] and "bores"[]
    "coaxial"[]."kind". fusion_screws: "screws", "washers", "passing". fusion_bearings: "bearings", "near_bearings".
    Every one of the four units must have facts before the answer is "none": a unit that never ran proves nothing."""
    units = {k: get(k) for k in ('fusion_near', 'fusion_census', 'fusion_screws', 'fusion_bearings')}
    if units['fusion_near'] is None:
        return None, 'the unit fusion_near has no facts for this geometry'
    missing = [k for k in ('fusion_census', 'fusion_screws', 'fusion_bearings') if units[k] is None]
    near, c, s, b = units['fusion_near'], units['fusion_census'] or {}, units['fusion_screws'] or {}, units['fusion_bearings'] or {}
    found = []
    bodies = _rows(near, 'bodies')
    if bodies:
        found.append('%d bought, hardware or mesh body(ies) within %g mm (%s)' % (len(bodies), near.get('margin') or 0, _some(x.get('name') for x in bodies)))
    on_axis = _coaxial(_rows(c, 'holes') + _rows(c, 'bores'))
    if on_axis:
        found.append('%d bought or hardware body(ies) on the axis of a hole' % len(on_axis))
    n = _n(s.get('screws')) + _n(s.get('washers')) + _n(s.get('passing')) + _n(b.get('bearings')) + _n(b.get('near_bearings'))
    if n:
        found.append('%d screw(s), washer(s) or bearing(s) at this part' % n)
    if found:
        return False, '; '.join(found)
    if missing:                                      # nothing near by the boxes, and a unit that reads what belongs to the part never ran
        return None, 'the unit %s has no facts for this geometry' % missing[0]
    return True, 'no bought, hardware or mesh body has its bounding box within %g mm of the box of this part, and none sits on the axis of a hole' % (near.get('margin') or 0)


def _bought_shown(get, params, tables):
    """What the facts SHOW of bought parts this part touches, or None: screws, washers and bearings that belong to it,
    and bought or hardware bodies on the axis of its holes. A body that is only near (by its bounding box) is not in
    this list."""
    s, b, c = get('fusion_screws') or {}, get('fusion_bearings') or {}, get('fusion_census') or {}
    found = []
    n = _n(s.get('screws')) + _n(s.get('washers')) + _n(b.get('bearings'))
    if n:
        found.append('%d screw(s), washer(s) or bearing(s) at this part' % n)
    on_axis = _coaxial(_rows(c, 'holes') + _rows(c, 'bores'))
    if on_axis:
        found.append('%d bought or hardware body(ies) on the axis of a hole (%s)' % (len(on_axis), _some(_leaf(x.get('name')) for x in on_axis)))
    return '; '.join(found) or None


def _non_table_shown(get, params, tables):
    """What the facts SHOW at this part that is no standard part drawn to its table, as a list of texts: bearings of
    the bearing unit (at the part, near it, a seat with no bearing, a ring it refused), bodies a hardware name picked
    and the shape refused, bodies on the axis of a screw hole or insert pocket that nothing recognised, bought or mesh
    bodies on the axis of a hole, hardware on an axis that is no fastener by its own name, and bodies near or on an
    axis that match motors.ref_pattern."""
    s, b, c = get('fusion_screws') or {}, get('fusion_bearings') or {}, get('fusion_census') or {}
    found = []
    n = _n(b.get('bearings')) + _n(b.get('near_bearings'))
    if n:
        found.append('%d bearing(s) at or near this part: a bearing is asked' % n)
    if _n(b.get('seats_without_bearing')):
        found.append('%d bearing seat(s) with no bearing in it' % _n(b.get('seats_without_bearing')))
    refused = _n(b.get('rejected')) or _n(b.get('not_bearings'))
    if refused:
        found.append('%d body(ies) near that a bearing name picked and the shape refused' % refused)
    if _n(s.get('rejected')):
        found.append('%d body(ies) near the part that a hardware name picked and the shape refused (%s): it is not known what they are'
                     % (_n(s.get('rejected')), _some(_leaf(r.get('name')) for r in _rows(s, 'rejected'))))
    blind = [r for r in _rows(s, 'fastener_holes') if r.get('bodies')]
    if blind:
        found.append('%d screw hole(s) or insert pocket(s) with a body on the axis that nothing recognised (%s)'
                     % (len(blind), _some(_leaf(x) for r in blind for x in r.get('bodies') or [])))
    other = [x.get('name') for x in _coaxial(_rows(c, 'holes') + _rows(c, 'bores'), ('bought', 'hardware', 'mesh'))
             if not _standard_body(x, params, tables)]
    if other:
        found.append('%d body(ies) on the axis of a hole that are no standard part by their name (%s)' % (len(other), _some(_leaf(x) for x in other)))
    got = _motor_hits(get, params, tables)
    if got and got[0]:
        found.append(_motor_text(got[0], got[2]))
    return found


def _only_table_parts(get, params, tables):
    """Reads fusion_near: "bodies"[] "kind", "name". fusion_census: "holes"[] and "bores"[] "coaxial"[]. fusion_screws:
    "screws", "washers", "passing", "rejected", "fastener_holes". fusion_bearings: "bearings", "near_bearings",
    "seats_without_bearing", "rejected", "not_bearings". Parameters: FASTENER_PATTERNS, motors.ref_pattern.
    True when the four units have facts, at least one standard part is there, nothing of _non_table_shown is shown,
    and every body within the near margin is a standard part by its own name (_standard_body: kind "hardware" and a
    designation of its table in its name): a bought or mesh body there, or a hardware body that is no standard part by
    its name (a bearing, a magnet in a component of screws, a connector "4 pin"), can be what the question is about.
    The kind the probes gave is not enough: a probe calls a body hardware for a word of its component or for "pin".
    A screw, washer, nut, insert or dowel is drawn to the size of its table: it has no STEP file to come from."""
    units = {k: get(k) for k in ('fusion_near', 'fusion_census', 'fusion_screws', 'fusion_bearings')}
    missing = [k for k in ('fusion_near', 'fusion_census', 'fusion_screws', 'fusion_bearings') if units[k] is None]
    if missing:
        return None, 'the unit %s has no facts for this geometry' % missing[0]
    near, s = units['fusion_near'], units['fusion_screws']
    found = _non_table_shown(get, params, tables)
    bodies = _rows(near, 'bodies')
    loose = [x.get('name') for x in bodies if not _standard_body(x, params, tables)]
    if loose:
        found.append('%d body(ies) within %g mm that are no fastener by their name (%s): not proven that none is a bought part with a STEP file'
                     % (len(loose), near.get('margin') or 0, _some(_leaf(x) for x in loose)))
    if found:
        return False, '; '.join(found)
    standard = len(bodies) + _n(s.get('screws')) + _n(s.get('washers')) + _n(s.get('passing'))
    if not standard:
        return False, 'no standard part at or near this part either: nothing is drawn to a table here'
    return True, ('every one of the %d body(ies) within %g mm of this part is a screw, washer, nut, insert, pin or dowel by its name, no unit lists a bearing, '
                  'a refused body or an unrecognised body on a screw axis, and nothing on an axis or near matches motors.ref_pattern'
                  % (len(bodies), near.get('margin') or 0))


TOUCH_GAP = 0.05     # mm between two bounding boxes that count as touching (fusion_near "gap": 0.0 = the boxes touch or overlap)


def _only_table_shown(get, params, tables):
    """What the facts SHOW against "only standard parts", or None: _non_table_shown, and every body of the near unit
    whose box touches the part's box (gap up to TOUCH_GAP) and that is no standard part by its own name (a board, a
    magnet, a belt, a switch). A body farther off within the margin makes the question asked, not the label false."""
    found = _non_table_shown(get, params, tables)
    near = get('fusion_near') or {}
    touch = [x.get('name') for x in _rows(near, 'bodies') if not _standard_body(x, params, tables)
             and not (isinstance(x.get('gap'), (int, float)) and not isinstance(x.get('gap'), bool) and x['gap'] > TOUCH_GAP)]
    if touch:
        found.append('%d body(ies) whose box touches this part that are no standard part by their name (%s)' % (len(touch), _some(_leaf(x) for x in touch)))
    return '; '.join(found) or None


def _standard_body(x, params, tables):
    """Is a body of the near unit or a census "coaxial" row a standard part: kind "hardware" and a standard part by its
    own name (_standard_part). A kind alone is not: the probes call a body hardware for a word of its component."""
    return isinstance(x, dict) and x.get('kind') == 'hardware' and _standard_part(x.get('name'), params, tables) is not None


# Words that make a name no standard part, whatever designation stands next to them: a magnet "D4x10", a pin header
# "1x4", a connector with "4 pin", a lead screw "T8x8", a standoff "M3x10" are bought parts with a model of their own.
_NOT_STANDARD = re.compile(r'(?i)magnet|belt|\bgt\d|\d\s*gt\b|pulley|switch|board|header|\bpcb|sensor|encoder|connector|\bjst\b|dupont|terminal|'
                           r'lead\s*screw|ball\s*screw|trapez|\btr\d|spring|gear|bearing|cable|wire|standoff|spacer|sleeve|bushing|shaft|axle|'
                           r'\brods?\b|tube|profile|extrusion|\bleds?\b|module|battery|\bfans?\b|motor|stepper|servo|plug|socket(?!\s*(?:head|cap))')
# DIN and ISO numbers of screws, washers, nuts, wood screws and pins, beside the ones the tables name in their texts
_STANDARD_NUMBERS = {'DIN': {'7', '84', '85', '95', '96', '97', '125', '127', '433', '439', '571', '557', '562', '912', '913', '914', '915', '916', '931',
                             '933', '934', '936', '963', '965', '985', '6325', '6912', '6921', '6923', '6798', '7349', '7380', '7981', '7982',
                             '7983', '7984', '7985', '7991', '7997', '9021'},
                     'ISO': {'1207', '1580', '2009', '2010', '2338', '4014', '4017', '4026', '4027', '4028', '4029', '4032', '4033', '4035', '4762',
                             '7040', '7045', '7046', '7047', '7049', '7050', '7051', '7089', '7090', '7092', '7093', '7380', '8734', '8735',
                             '10511', '10642', '14579', '14580', '14583'}}
_PIN_NUMBERS = {('DIN', '7'), ('DIN', '6325'), ('ISO', '2338'), ('ISO', '8734'), ('ISO', '8735')}
_THREAD = re.compile(r'(?<![A-Za-z0-9.])[Mm](\d+(?:\.\d+)?)(?![\d.])')
_SIZE = re.compile(r'(?<![A-Za-z0-9.])[DdØ]?(\d+(?:\.\d+)?)\s*(?:[a-zA-Z]{1,2}\d{1,2})?\s*[xX×]\s*\d')
_NUMBER = re.compile(r'(?i)\b(DIN|ISO)\s*-?\s*(\d+)\b')
_TABLE_NUMBERS = {}


def _standard_numbers(tables):
    """{(DIN|ISO, number)} of the standard parts: the built-in ones and every DIN or ISO number the screws, washers,
    pins and inserts tables name in their texts."""
    key = id(tables)
    if key not in _TABLE_NUMBERS:
        out = {(k, n) for k, ns in _STANDARD_NUMBERS.items() for n in ns}
        stack = [(tables or {}).get(t) for t in ('screws', 'washers', 'pins', 'inserts')]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, str):
                out |= {(m.group(1).upper(), m.group(2)) for m in _NUMBER.finditer(x)}
        _TABLE_NUMBERS.clear()
        _TABLE_NUMBERS[key] = out
    return _TABLE_NUMBERS[key]


def _table_threads(tables):
    """The threads ("M3") the screws, washers and inserts tables hold, or None with no table at all (then any thread)."""
    t = tables or {}
    out = {str(k).upper() for name in ('screws', 'washers') for k in ((t.get(name) or {}).get('sizes') or {})}
    out |= {str(v.get('thread')).upper() for v in ((t.get('inserts') or {}).get('kinds') or {}).values() if isinstance(v, dict) and v.get('thread')}
    return out or None


def _pin_sizes(tables):
    return {float(v['d']) for v in (((tables or {}).get('pins') or {}).get('sizes') or {}).values() if isinstance(v, dict) and isinstance(v.get('d'), (int, float))}


def _standard_part(name, params, tables):
    """What standard part a body is by its own name: "screw", "washer", "nut", "insert", "wood screw" or "dowel", or
    None. Read is the body's name without its component (the text after the last " / ") and without what stands in
    parentheses ("(motor to plate)" says where it goes, not what it is). A name is a standard part only with its
    designation: a thread the screws, washers or inserts table holds ("M3x12", "insert M3x4x5", "washer M3"), a DIN
    or ISO number of a screw, washer, nut or pin ("DIN 912", "ISO 8734 4x20"), or for a wood screw or a dowel its size
    ("wood screw 4x30", "dowel 8x60" of a diameter of the pins table). A loose word is never enough: "pin", "rod",
    "shaft" or "screw" alone say nothing, and a name that matches motors.ref_pattern or speaks of a magnet, board,
    header, connector, belt, switch, bearing, lead screw, standoff or the like (_NOT_STANDARD) is none at all."""
    bare = re.sub(r'\([^)]*\)', ' ', _leaf(name)).strip()
    if not bare or _matches(params.get('motors.ref_pattern'), bare) or _NOT_STANDARD.search(bare) or _matches(params.get('bearings.ref_pattern'), bare):
        return None
    known = _table_threads(tables)
    thread = any(known is None or ('M' + m.group(1)).upper() in known for m in _THREAD.finditer(bare))
    numbers = {(m.group(1).upper(), m.group(2)) for m in _NUMBER.finditer(bare)} & _standard_numbers(tables)
    size = _SIZE.search(bare)
    if _matches(params.get('inserts.ref_pattern'), bare):
        return 'insert' if thread else None
    if _matches(params.get('washers.ref_pattern'), bare):
        return 'washer' if thread or numbers else None
    if _matches(params.get('screws.nut_ref_pattern'), bare):
        return 'nut' if thread or numbers else None
    if numbers & _PIN_NUMBERS or re.search(r'(?i)(?<![a-z])dowels?(?![a-z])', bare):
        pins = _pin_sizes(tables)
        d = float(size.group(1)) if size else None
        return 'dowel' if (numbers & _PIN_NUMBERS and size) or (d is not None and (not pins or any(abs(d - p) < 1e-6 for p in pins))) else None
    if _matches(params.get('screws.wood_pattern'), bare):
        return 'wood screw' if size or numbers else None
    if thread and (_matches(params.get('screws.ref_pattern'), bare) or numbers):
        return 'screw'
    return None


def _fastener_name(name, params, tables=None):
    """Is a body a standard part by its own name (_standard_part)? `tables` None: the shipped tables are not at hand,
    any thread counts and a dowel of any diameter."""
    return _standard_part(name, params, tables) is not None


def _bearing_body(x, params, tables):
    """Is a body near a bearing by its name: of kind "hardware", its own name (or its component) speaks of a bearing
    (_bearing_name) and nothing in its own name says it is anything else (_NOT_STANDARD, motors.ref_pattern)."""
    if x.get('kind') != 'hardware':
        return False
    bare = re.sub(r'\([^)]*\)', ' ', _leaf(x.get('name')))
    other = _NOT_STANDARD.search(re.sub(r'(?i)bearings?', ' ', bare))
    return not other and not _matches(params.get('motors.ref_pattern'), bare) and _bearing_name(x.get('name'), params, _bearing_names(tables))


def _screw_unit_names(get):
    """The names of the bodies the screw unit lists as screws, washers or screws that pass the part."""
    s = get('fusion_screws') or {}
    out = set()
    for key in ('screws', 'washers', 'passing'):
        for r in _rows(s, key):
            for k in ('ref', 'name', 'head', 'shank'):
                if isinstance(r.get(k), str) and r[k]:
                    out.add(r[k])
    return out


def _motor_hits(get, params, tables=None):
    """(names of the bodies near or on an axis that match motors.ref_pattern, the near facts, the pattern), or None
    when the near unit has no facts or the pattern cannot be read.
    A body of kind "hardware" that is a standard part by its own name (_standard_body) or a row of the screw unit is
    never a hit: a screw "M3x12 (motor to plate)" in a component "motor screws" is a screw. A body of another kind is a
    hit by its path whatever its own name says: the "Shaft" or the "M3 screw" of a stepper model is that stepper. A
    body of kind "hardware" is held against its own name and component only, not against the components above it."""
    near = get('fusion_near')
    pattern = params.get('motors.ref_pattern')
    try:
        re.compile(pattern)
    except (re.error, TypeError):
        return None
    if near is None:
        return None
    c = get('fusion_census') or {}
    skip = _screw_unit_names(get)
    # the pattern is held against the whole path: the bodies of a motor model are named "Rotor", the motor is named above them
    named = [(('%s %s' % (x.get('path') or '', x.get('name') or '')) if x.get('kind') != 'hardware' else str(x.get('name') or ''), x.get('name'))
             for x in _rows(near, 'bodies')]
    named += [(str(x.get('name') or ''), x.get('name')) for x in _coaxial(_rows(c, 'holes') + _rows(c, 'bores'), ('bought', 'hardware', 'mesh'))]
    rows = [x for x in _rows(near, 'bodies')] + [x for x in _coaxial(_rows(c, 'holes') + _rows(c, 'bores'), ('bought', 'hardware', 'mesh'))]
    return [name for (text, name), x in zip(named, rows) if name not in skip and not _standard_body(x, params, tables)
            and (_matches(pattern, text) or _CONNECTOR.search(re.sub(r'\([^)]*\)', ' ', text if x.get('kind') != 'hardware' else _leaf(name))))], near, pattern


def _motor_text(hits, pattern):
    """The text of the hits of _motor_hits(): the ones the pattern found, then the connectors by their own name."""
    plug = [h for h in hits if not _matches(pattern, str(h or '')) and _CONNECTOR.search(re.sub(r'\([^)]*\)', ' ', str(h or '')))]
    named = [h for h in hits if h not in plug]
    out = []
    if named or not plug:
        out.append('%d body(ies) near or on an axis match motors.ref_pattern (%s)' % (len(named), _some(_leaf(h) for h in named)))
    if plug:
        out.append('%d body(ies) near or on an axis are a connector by their own name (%s)' % (len(plug), _some(_leaf(h) for h in plug)))
    return '; '.join(out)


# a connector by its own name, whatever motors.ref_pattern says: "JST XH 4 pin", "Pin header 1x4", "Dupont 2.54", the
# power plugs "XT60 male", "Wago 221-413", "DC barrel 5.5x2.1", "DC jack"
_CONNECTOR = re.compile(r'(?i)connector|\bjst|dupont|\bheaders?\b|terminal|molex|\bplugs?\b|\b(?:xh|ph|zh|sh|gh)\s*\d|\bgx\s*\d|\bdc[\s-]*(?:jack|socket|barrel)|'
                        r'\busb\b|\bxt\s*-?\s*(?:30|60|90)|\bwago\b|\bbarrel[\s-]*(?:jack|plug|socket|connector|\d)|\b(?:audio|power|barrel)[\s-]*jack')


def _no_motor_near(get, params, tables):
    """Reads fusion_near: "bodies"[] "name", "path", "kind", "margin". fusion_census (when it has facts): "holes"[] and
    "bores"[] "coaxial"[]."name". The parameter motors.ref_pattern decides what a motor or a connector is.
    A name can only make this test False. It is True when no body near matches the pattern or is a connector by its
    own name (_CONNECTOR), and every body near is a standard part by its designation (_standard_body) or a bearing by
    its name (_bearing_body): any other body (the "Rotor" of a motor model, a servo named by its type, a "Shaft" of a
    stepper, a magnet in a component of screws) can be a motor or a connector, and no unit reads that from its shape.
    The kind a probe gave a body ("hardware" for a word of its component, or for "pin") is not enough."""
    if get('fusion_near') is None:
        return None, 'the unit fusion_near has no facts for this geometry'
    got = _motor_hits(get, params, tables)
    if got is None:
        return None, 'the parameter motors.ref_pattern is not a pattern that can be read'
    hits, near, _ = got
    if hits:
        return False, _motor_text(hits, got[2])
    unknown = [x for x in _rows(near, 'bodies') if not _standard_body(x, params, tables) and not _bearing_body(x, params, tables)]
    if unknown:
        return False, ('%d body(ies) within %g mm that are no standard part and no bearing by their name (%s): not proven that none is a motor or a connector'
                       % (len(unknown), near.get('margin') or 0, _some(x.get('name') for x in unknown)))
    return True, ('none of the %d bodies within %g mm of this part matches motors.ref_pattern, and each of them is a standard part or a bearing by its name'
                  % (len(_rows(near, 'bodies')), near.get('margin') or 0))


def _motor_shown(get, params, tables):
    """What the facts SHOW against "no motor and no connector", or None: bodies near or on an axis whose name or
    component matches motors.ref_pattern, or whose own name is that of a connector (_CONNECTOR); and, as for the
    standard-parts label of hardware.bought_from_step, every body of the near unit whose box touches the part's box
    (gap up to TOUCH_GAP) that is no standard part and no bearing by its designation and no row of the screw unit: a
    servo, a stepper or a plug under a name no pattern knows sits right at the part. A body farther off within the
    margin keeps the question asked (_no_motor_near) and is no proof against the label."""
    got = _motor_hits(get, params, tables)
    if got is None:
        return None
    hits, near, pattern = got
    out = [_motor_text(hits, pattern)] if hits else []
    skip = _screw_unit_names(get) | {str(h) for h in hits}
    touch = [x.get('name') for x in _rows(near, 'bodies') if str(x.get('name')) not in skip
             and not _standard_body(x, params, tables) and not _bearing_body(x, params, tables)
             and not (isinstance(x.get('gap'), (int, float)) and not isinstance(x.get('gap'), bool) and x['gap'] > TOUCH_GAP)]
    if touch:
        out.append('%d body(ies) whose box touches this part that are no standard part and no bearing by their name (%s): not proven that none is a motor or a connector'
                   % (len(touch), _some(_leaf(n) for n in touch)))
    return '; '.join(out) or None


PRINT_FRAME = 'print_frame'     # no probe: gate_plan.print_frame() of the part, for the fact tests that need its bed face


def _bed_face_measured(get, params, tables):
    """Proven when the bed face of the part is MEASURED on a saved .3mf with THIS geometry (gate_plan.print_frame
    "from": "project": an object of a registered project is the part's STL turned as a rigid body). False when only a
    build table says it, and also for "earlier geometry" (the project still places the mesh measured before this STL
    was exported again): the face is known, but what faces down on the NEW geometry was never placed, pre-checked
    (print.overhang_angle) or sliced, so the label "judged there" would be ahead of the facts. None when the bed face
    is not known at all."""
    f = get(PRINT_FRAME)
    frames = f.get('frames') if isinstance(f, dict) and isinstance(f.get('frames'), list) else []
    if len(frames) > 1 and all(isinstance(x, dict) and isinstance(x.get('up'), list) for x in frames):
        if all(x.get('from') == 'project' for x in frames):          # every placement measured on this geometry
            return True, 'measured on the saved projects, on %d faces: %s' % (len(frames), f.get('source'))
        off = [x for x in frames if x.get('from') != 'project']
        return False, ('the part lies on %d faces, and %d of them are not measured on this geometry (%s)'
                       % (len(frames), len(off), '; '.join('%s: %s' % (x.get('from'), x.get('source')) for x in off)))
    if not isinstance(f, dict) or not isinstance(f.get('up'), list):
        return None, 'the bed face is not known: %s' % ((f or {}).get('why') if isinstance(f, dict) else 'it could not be worked out')
    if f.get('from') == 'earlier geometry':
        return False, ('the project still places the geometry before this export (%s): the new geometry is not placed, pre-checked or sliced yet'
                       % (f.get('source'),))
    if f.get('from') != 'project':
        return False, 'only the build table says the bed face (%s): no saved project holds this part yet' % (f.get('source'),)
    return True, 'measured on the saved project: %s' % (f.get('source'),)


def _bed_face_shown(get, params, tables):
    """Nothing the facts show makes the measured answer false: it holds only while it is measured (fact_answer)."""
    return None


FACT_TESTS = {'no_screw_feature': _no_screw_feature, 'no_bearing_here': _no_bearing_here, 'no_bought_near': _no_bought_near,
              'no_motor_near': _no_motor_near, 'bed_face_measured': _bed_face_measured, 'only_table_parts_near': _only_table_parts}
# What the facts SHOW, per test: a text, or None. A fact test is False both for "the facts show it" and for "it could be
# there and nothing proves that it is not". Only the first makes an answer false: an answer that says the question does
# not apply is refused, and a stored one is asked again, while the function here gives a text (answer_contradicted).
FACT_SHOWN = {'no_screw_feature': _screws_shown, 'no_bearing_here': _bearing_shown, 'no_bought_near': _bought_shown,
              'no_motor_near': _motor_shown, 'bed_face_measured': _bed_face_shown, 'only_table_parts_near': _only_table_shown}
# the probes each test reads. gate_plan plans a unit for every one of them on a part that a question with "auto" is asked for
TEST_PROBES = {'no_screw_feature': ('fusion_screws', 'fusion_census'), 'no_bearing_here': ('fusion_bearings', 'fusion_census', 'fusion_near'),
               'no_bought_near': ('fusion_near', 'fusion_census', 'fusion_screws', 'fusion_bearings'), 'no_motor_near': ('fusion_near', 'fusion_census'),
               'bed_face_measured': (), 'only_table_parts_near': ('fusion_near', 'fusion_census', 'fusion_screws', 'fusion_bearings')}
_PARAMS_CACHE = {}


def _fact_params(root, reg):
    """The values of FACT_PARAMS for this project (the registry defaults when the project's parameters cannot be read)."""
    import gate_params
    path = gate_common.gate_path(root, 'owner', 'params.json')
    key = (os.path.abspath(root), os.path.getmtime(path) if os.path.isfile(path) else None, reg.get('sha256'), id(reg.get('params')))
    if key not in _PARAMS_CACHE:
        names = [n for n in FACT_PARAMS if n in reg['params']]
        try:
            vals = gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), None), names)
        except GateError:
            vals = {n: reg['params'][n].get('default') for n in names}
        _PARAMS_CACHE.clear()
        _PARAMS_CACHE[key] = vals
    return _PARAMS_CACHE[key]


def _getter(root, part, sig):
    """get(probe) -> the facts of that probe of the part at this signature (None when the unit did not run); for
    PRINT_FRAME, gate_plan.print_frame() of the part."""
    import gate_plan
    data, cache = gate_common.load_facts(root, part, sig), {}

    def get(probe):
        if probe not in cache:
            if probe == PRINT_FRAME:
                try:
                    entry = gate_plan.part_entry(root, part)
                    cache[probe] = gate_plan.print_frame(root, part, entry)
                except Exception as e:             # unknown is unknown: the test says None
                    cache[probe] = {'up': None, 'why': '%s: %s' % (type(e).__name__, e)}
            else:
                cache[probe] = gate_plan._probe_facts(data, probe)
        return cache[probe]
    return get


def fact_tests(root, reg, rule, part, sig):
    """What the facts say about each entry of the rule's attest.auto: [{"gives": {"label": ...} or {"na": ...},
    "tests": [{"test", "state": True | False | None, "text"}], "proven": bool}]. [] for a rule with no "auto"."""
    import gate_plan
    auto = (rule.get('attest') or {}).get('auto')
    if not isinstance(auto, list) or not sig:
        return []
    get = _getter(root, part, sig)
    params, out = _fact_params(root, reg), []
    for entry in auto:
        tests = []
        for name in entry.get('when') or []:
            fn = FACT_TESTS.get(name)
            state, text = fn(get, params, reg.get('tables') or {}) if fn else (None, 'the fact test %s is not built' % name)
            tests.append({'test': name, 'state': state, 'text': text})
        out.append({'gives': {k: entry[k] for k in ('label', 'na') if k in entry}, 'tests': tests,
                    'proven': bool(tests) and all(t['state'] is True for t in tests)})
    return out


def fact_answer(root, reg, rule, part, sig):
    """The answer the facts of the part give to a part-level question, or None when they prove nothing:
    {"kind": "facts", "as": "label" | "na", "value": the label or the reason, "proof": what the facts say,
     "tests": [names]}. The first entry of attest.auto whose every test is proven wins."""
    for entry in fact_tests(root, reg, rule, part, sig):
        if entry['proven']:
            how = 'label' if 'label' in entry['gives'] else 'na'
            return {'kind': FACTS, 'as': how, 'value': entry['gives'][how], 'proof': '; '.join(t['text'] for t in entry['tests']),
                    'tests': [t['test'] for t in entry['tests']]}
    return None


def answer_contradicted(root, reg, rule, part, sig, kind, value):
    """Why the facts of the part at this signature contradict an answer that says the question does not apply, or None.

    Such an answer is a label that an entry of the rule's attest.auto gives ("this part has no screws"), and any "na"
    on a rule that has attest.auto. It is contradicted while a fact test of that entry SHOWS the thing (FACT_SHOWN: two
    screws at the part, a screw hole with a bought body on its axis that nothing recognised). A hole that a screw only
    could use contradicts nothing: then the question is asked and the answer is the agent's word.
    rule: the registry rule, or its id."""
    import gate_plan
    if isinstance(rule, str):
        rule = (reg.get('rules') or {}).get(rule) or {}
    auto = (rule.get('attest') or {}).get('auto')
    if kind not in ('label', 'na') or not isinstance(auto, list) or not sig:
        return None
    entries = [e for e in auto if isinstance(e, dict) and (kind == 'na' or e.get('label') == value)]
    if not entries:
        return None
    get = _getter(root, part, sig)
    params, tables = _fact_params(root, reg), reg.get('tables') or {}
    for entry in entries:
        for name in entry.get('when') or []:
            fn = FACT_SHOWN.get(name)
            text = fn(get, params, tables) if fn else None
            if text:
                return text
    return None


def stored_contradicted(root, reg, rule, part, sig, kind, value):
    """Why a stored answer to a question about the whole part does not hold today, or None: a label that the rule's
    attest.held refuses on today's facts (label_contradicted), or an answer that says the question does not apply
    while the facts show that it does (answer_contradicted). Facts can change under one signature: a unit is run again
    and now recognises the screws it missed."""
    if kind == 'label':
        why = label_contradicted(root, reg, {'rule': rule if isinstance(rule, str) else rule.get('id'), 'part': part}, value, sig)
        if why:
            return why
    return answer_contradicted(root, reg, rule, part, sig, kind, value)


def record_fact_answers(root, reg, part, sig, run=None):
    """Bring the stored answers of kind "facts" of one part in step with its facts at this signature: store the ones
    that are proven now, drop the ones of this signature that are proven no more. gate_plan.judge_part() calls it, so a
    stamp can name such an answer and gate_stamp.verify() finds it in gate/attest like any other. Returns how many are
    stored for this signature."""
    if not sig:
        return 0
    context, anchor = gate_common.sig12(sig), gate_features.part_anchor()
    want = {}
    for rule in gate_registry.rules(reg, level='attest', deferred=False):
        a = rule.get('attest') or {}
        if a.get('per') == 'part' and a.get('auto'):
            got = fact_answer(root, reg, rule, part, sig)
            if got:
                want[rule['id']] = got
    path = gate_common.attest_path(root, part)
    data = read_json(path, {})
    data = data if isinstance(data, dict) else {}
    old = [a for a in data.get('answers') or [] if isinstance(a, dict)]
    mine = {a.get('rule'): a for a in old if a.get('kind') == FACTS and a.get('context') == context}
    same = set(mine) == set(want) and all(mine[r].get('value') == want[r]['value'] and mine[r].get('proof') == want[r]['proof'] for r in want)
    if same:
        return len(want)
    keep = [a for a in old if not (a.get('kind') == FACTS and a.get('context') == context)]
    for rid in sorted(want):
        keep.append(dict(want[rid], q=question_id(rid, part, anchor, context), rule=rid, part=part, anchor=anchor, context=context, sig=sig,
                         time=gate_common.now(), run=run))
    if keep or os.path.isfile(path):
        write_json(path, dict(data, part=part, answers=keep))
    return len(want)


def feature_text(anchor):
    """Where a feature is, for a question: 'at (10, 0, 6)'; 'of the whole part' for the part anchor."""
    if not isinstance(anchor, dict) or anchor.get('kind') == 'part':
        return 'of the whole part'
    try:
        return 'at (%s)' % ', '.join('%g' % float(c) for c in anchor.get('point') or [])
    except (TypeError, ValueError):
        return gate_features.anchor_key(anchor)


def _question(rule, part, anchor, context, text, finding=None):
    a = rule.get('attest') or {}
    return {'q': question_id(rule['id'], part, anchor, context), 'rule': rule['id'], 'part': part, 'anchor': anchor, 'context': context,
            'question': text, 'evidence': list(a.get('evidence') or []), 'labels': list(a.get('labels') or []), 'stage': rule.get('stage'),
            'finding': (finding or {}).get('id') or (gate_features.finding_id(finding) if finding else None),
            'finding_rule': (finding or {}).get('rule'), 'by': list((finding or {}).get('by') or []),
            'finding_text': (finding or {}).get('text') if finding else None, 'answer': None}


def questions_for_part(root, reg, part, sig, findings, stages=('design',)):
    """Every question of one part at one signature, answered or not.

    reg       gate_registry.load()
    findings  the part's current findings (those with "ask" become questions)
    stages    part-level attest rules of these stages are asked

    Returns a list of questions (see the top), each with "answer" filled from gate/attest when one matches
    (find_answer). A deferred rule asks nothing. A rule that does not apply to the part's source asks nothing.

    A part-level question whose rule has "auto" and that nobody answered is held against the facts of the part
    (fact_answer): proven, its "answer" is {"kind": "facts", ...} and nothing is asked. Not proven, it stays open and
    carries "facts": the texts of what the facts show or cannot say, so the reader sees why it is asked."""
    import gate_plan
    try:
        source = gate_plan.part_entry(root, part).get('source', 'fusion')
    except GateError:
        source = 'fusion'
    answers, waivers, out, seen = load_answers(root, part), _owner_waivers(root), [], set()
    for rule in gate_registry.rules(reg, level='attest', deferred=False):
        a = rule.get('attest') or {}
        if a.get('per') != 'part' or rule['stage'] not in stages or not gate_registry.applies_to(rule, source):
            continue
        out.append(_question(rule, part, gate_features.part_anchor(), gate_common.sig12(sig) if sig else '', a.get('question', '')))
    for f in findings or []:
        if not f.get('ask'):
            continue
        rule = reg['rules'].get(f['ask'])
        if rule is None or rule.get('level') != 'attest' or rule.get('deferred'):
            # a finding that asks for a question nobody registered stays open: shown, and answerable only by a waiver
            rule = {'id': f['ask'], 'stage': None, 'attest': {'question': 'Finding %s asks for the rule %s, which is not an attest rule of the registry.'
                                                              % (f.get('rule'), f['ask']), 'evidence': ['waiver'], 'labels': []}}
        text = (rule['attest'].get('question') or '').replace('{feature}', feature_text(f.get('anchor')))
        text = text.replace('{by}', ', '.join(f.get('by') or []) or 'nothing named')
        q = _question(rule, part, f.get('anchor') or gate_features.part_anchor(), f.get('context'), text, finding=f)
        if q['q'] in seen:
            continue
        seen.add(q['q'])
        out.append(q)
    for q in out:
        live = [a for a in answers if a.get('kind') != FACTS and (a.get('kind') != 'waiver' or _waiver_holds(waivers, a, q))]
        q['answer'] = find_answer(live, q['rule'], q['anchor'], q['context'])
        rule = reg['rules'].get(q['rule']) or {}
        gone = None
        if q['answer'] is not None and not q.get('finding') and sig:
            # an answer about the whole part holds while the facts do not contradict it: the question comes back when they do
            why = stored_contradicted(root, reg, rule, part, sig, q['answer'].get('kind'), q['answer'].get('value'))
            if why:
                gone = 'the answer "%s" given before does not hold any more: %s' % (q['answer'].get('value'), why)
                q['answer'] = None
        if q['answer'] is None and not q.get('finding') and (rule.get('attest') or {}).get('auto') and sig:
            tests = fact_tests(root, reg, rule, part, sig)
            proven = next((e for e in tests if e['proven']), None)
            if proven and not gone:
                how = 'label' if 'label' in proven['gives'] else 'na'
                stored = next((a for a in answers if a.get('kind') == FACTS and a.get('q') == q['q']), None) or {}
                q['answer'] = {'q': q['q'], 'rule': q['rule'], 'part': part, 'anchor': q['anchor'], 'context': q['context'], 'kind': FACTS,
                               'as': how, 'value': proven['gives'][how], 'proof': '; '.join(t['text'] for t in proven['tests']),
                               'sig': sig, 'time': stored.get('time'), 'run': stored.get('run')}
            else:
                q['facts'] = [t['text'] for e in tests for t in e['tests'] if t['state'] is not True]
        if gone:
            q['facts'] = [gone] + list(q.get('facts') or [])
    return out


def _touched(root, census):
    """Parts of the census that a run or an export has looked at in their current geometry."""
    out = []
    for pid in sorted(census['parts']):
        e = census['parts'][pid]
        if e.get('gone') or not e.get('sig'):
            continue
        if (read_json(gate_common.facts_path(root, pid, e['sig']), {}) or {}).get('judged'):
            out.append(pid)
    return out


def open_questions(root, reg, parts=None, stages=('design', 'export'), waiting=False):
    """The unanswered questions of these parts (default: every part of the census that is not gone), sorted by part and
    rule. Reads stored findings; runs nothing.

    With parts None only the parts a run or an export has judged in their current geometry are asked about: a part
    nobody looked at yet has no findings to ask about, and its part-level questions come with its first run.
    The questions of an inherited part (see the top) are left out; with `waiting` they are in the list too, each marked
    "waiting": True."""
    import gate_plan
    census = gate_common.load_census(root)
    if parts is None:
        parts = _touched(root, census)
    out = []
    for pid in parts:
        if pid not in census['parts']:
            raise GateError('unknown part %r: it is not in gate/census.json' % (pid,))
        v = gate_plan.part_verdict(root, reg, pid, stages=stages, defer_inherited=True)
        out += v['questions'] + v['ask_questions']
        if waiting:
            out += [dict(q, waiting=True) for q in v.get('deferred_questions') or []]
    out.sort(key=lambda q: (q['part'], q['rule'], q['q']))
    return out


def _run_start(root, part, sig):
    """Epoch seconds of the start of the part's latest run: the newest plan under gate/run/ that names the part, else the
    first time facts of this geometry were stored. None when the part was never looked at."""
    import calendar, time
    best = None

    def stamp(text):
        try:
            return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))
        except (TypeError, ValueError):
            return None
    run_root = gate_common.gate_path(root, 'run')
    if os.path.isdir(run_root):
        for name in os.listdir(run_root):
            plan = os.path.join(run_root, name, 'plan.json')
            if os.path.isfile(plan):
                try:
                    data = read_json(plan, {})
                except GateError:
                    continue
                t = stamp(data.get('created')) if part in (data.get('parts') or []) else None
                if t is not None and (best is None or t > best):
                    best = t
    if best is None and sig:
        times = [stamp(p.get('time')) for p in (gate_common.load_facts(root, part, sig).get('probes') or {}).values() if isinstance(p, dict)]
        times = [t for t in times if t is not None]
        best = min(times) if times else None
    return best


def _facts_of(root, part, probe, sig=None):
    """The usable facts of one probe at a signature of the part (default: its present one), or None (never run, an
    error, no such part)."""
    import gate_plan
    sig = sig or (gate_common.load_census(root)['parts'].get(part) or {}).get('sig')
    return gate_plan._probe_facts(gate_common.load_facts(root, part, sig), probe) if sig else None


def _count(root, part, path, sig=None):
    """How many rows the facts hold at "probe.list" or "probe.list.sublist" (the rows of `sublist` over every row of
    `list`), or None when that probe has no usable facts for the part."""
    bits = str(path).split('.')
    facts = _facts_of(root, part, bits[0], sig)
    if facts is None:
        return None
    rows = [facts]
    for key in bits[1:]:
        rows = [x for r in rows if isinstance(r, dict) for x in (r.get(key) if isinstance(r.get(key), list) else [])]
    return len(rows)


def label_contradicted(root, reg, question, label, sig=None):
    """Why the facts of the part contradict this label, or None. The rule's attest.held says what a label claims:
    {"<label>": {"none": ["fusion_screws.screws"]}} is refused while that list holds a row, {"some": [...]} while every
    list named is empty. Facts that were never measured contradict nothing: the label is then the only word.
    sig: the signature whose facts are read (default: the part's present one)."""
    held = ((reg['rules'].get(question.get('rule')) or {}).get('attest') or {}).get('held') or {}
    claim = held.get(label)
    if not isinstance(claim, dict):
        return None
    if claim.get('by') == 'some':                     # a question of a finding: the label speaks of what the finding names
        if not [n for n in question.get('by') or [] if str(n).strip()]:
            return 'the label speaks of a neighbour, seat or bearing this row names, and the row names none'
        return None
    if claim.get('by') == 'none':                     # a question of a finding: the label says there is nothing, the finding names something
        names = [str(n) for n in question.get('by') or []]
        if names:
            return 'the label says there is none, the finding names %d: %s%s' % (len(names), ', '.join(names[:3]), ' and %d more' % (len(names) - 3) if len(names) > 3 else '')
        return _touching_unseen(root, question, sig) if claim.get('untouched') is True else None
    if isinstance(claim.get('text'), dict):          # a question of a finding: the label fits only a finding of this kind
        return _text_refuses(claim['text'], question.get('finding_text'))
    if isinstance(claim.get('coaxial_room'), str):
        return _fitted_on_axis(root, reg, question, claim['coaxial_room'], sig) or (_touching_unseen(root, question, sig) if claim.get('untouched') is True else None)
    if claim.get('screw_seat') is True:
        return _no_head_seat(root, reg, question, sig)
    for path in claim.get('none') or []:
        n = _count(root, question['part'], path, sig)
        if n:
            return 'the label says there is none, the facts of this part list %d (%s)' % (n, path.replace('.', ': ', 1))
    some = [(path, _count(root, question['part'], path, sig)) for path in claim.get('some') or []]
    if some and all(n == 0 for _, n in some):
        return 'the label speaks of what this part has, the facts list none (%s)' % ', '.join(p.replace('.', ': ', 1) for p, _ in some)
    return None


def _text_refuses(want, text):
    """Why the text of the finding a question is about does not fit a label of attest.held {"text": {"has": regex,
    "not": regex}}, or None. A question with no finding text (an older stored question) is refused: the label is
    only for a finding of the kind the regex says."""
    text = text if isinstance(text, str) else ''
    if want.get('has') and not _matches(want['has'], text):
        return 'the label is only for a finding that says %s; this one says: %s' % (want.get('says') or 'what the label names', text[:200] or 'nothing')
    if want.get('not') and _matches(want['not'], text):
        return 'the label is not for a finding that says %s; this one says: %s' % (want.get('says_not') or 'that', text[:200])
    return None


def _question_hole(root, question, sig):
    """(the census hole the question is about, the census facts), or (None, facts) when it is not in the census."""
    facts = _facts_of(root, question['part'], 'fusion_census', sig) or {}
    holes = [h for h in (facts.get('holes') or []) if isinstance(h, dict)]
    hole = gate_features.find(question['anchor'], holes) if isinstance(question.get('anchor'), dict) and question['anchor'].get('kind') != 'part' else None
    return hole, facts


def _hole_segments(hole):
    """[(radius, from, to)] of the hole's segments along its axis: 0 at the mouth, negative inwards (the "span" frame
    of the census "coaxial" rows)."""
    out, t = [], 0.0
    for seg in hole.get('segments') or []:
        if not isinstance(seg, dict) or not isinstance(seg.get('d'), (int, float)) or not isinstance(seg.get('len'), (int, float)):
            return []
        r = max(seg['d'], seg.get('d2') or seg['d']) / 2.0 if isinstance(seg.get('d2'), (int, float)) else seg['d'] / 2.0
        out.append((r, -(t + seg['len']), -t))
        t += seg['len']
    return out


ENTER_MIN = gate_features.ENTER_MIN
enters = gate_features.coaxial_enters


# a fit class after a diameter in a name: "D25h7", "pilot D25 h7", "4m6x32", "8H7"; x, y and z are no fit letters (sizes)
_FIT = re.compile(r'(?<![A-Za-z0-9.])[DdØ]?\s*(\d+(?:\.\d+)?)\s*(js|JS|[a-hj-nprstuA-HJ-NPRSTU])(\d{1,2})(?![\d.]|[A-Za-z]{2})')
FIT_NAME_TOL = 0.5      # mm: a fit diameter in a name this close to the hole's diameter is the fit of that hole


def _named_fits(name, path=None):
    """[diameter] of the fit classes a body's name carries (_FIT): the whole name with its components ("gearbox G20 pilot
    D25h7 / Body1") and what stands in parentheses ("gearbox (pilot D25h7)"), and the occurrence path when the row has
    one. A fit is often named on the component or in a note; only its diameter decides which hole it is for."""
    text = ' '.join(str(t) for t in (path, name) if t)
    return [float(m.group(1)) for m in _FIT.finditer(text.replace('(', ' ').replace(')', ' '))]


def _fitted_on_axis(root, reg, question, room_param, sig):
    """Why "nothing bought is fitted in this hole" does not hold, or None. It holds only on measured facts; refused:
      - the hole is not in the census of the part (nothing can be held against it);
      - a bought or hardware body that reaches into the hole on its axis carries a fit class on the diameter of the
        hole in its name ("pilot D25h7" in a D25 step, "4m6x32" in a D4.02 hole): a named fit is a fit;
      - such a body has no measured radii (census "coaxial"[]."r": [[radius, from, to]], measured since 0.3.2): stored
        facts of an older census cannot show that it passes with room, so a re-measure (gate.py run) or another label;
      - such a body has a cylinder whose radius is within the parameter `room_param` of the hole wall where they
        overlap, or wider than the hole there (pressed in, or colliding with the wall: either way it does not pass).
    A held claim with "untouched": true is refused besides by _touching_unseen (label_contradicted)."""
    hole, _ = _question_hole(root, question, sig)
    if not isinstance(hole, dict):
        return 'the hole of this question is not in the census of the part: nothing shows that the body on its axis passes with room'
    room = _fact_params(root, reg).get(room_param)
    room = room if isinstance(room, (int, float)) and not isinstance(room, bool) else 0.15
    segs = _hole_segments(hole)
    depth = hole.get('depth') if isinstance(hole.get('depth'), (int, float)) else (-segs[-1][1] if segs else None)
    diameters = sorted({round(2 * R, 4) for R, _, _ in segs} | {round(h, 4) for h in [_narrowest(hole)] if isinstance(h, (int, float))})
    for x in _coaxial([hole]):
        if not enters(x.get('span'), depth):
            continue
        named = [d for d in _named_fits(x.get('name'), x.get('path')) if any(abs(d - D) <= FIT_NAME_TOL + 1e-9 for D in diameters)]
        if named:
            return ('%s carries the fit class of D%g in its name and the hole is D%s: a named fit is fitted in this hole. Name the fit parameter its size comes from'
                    % (x.get('name'), named[0], ', D'.join('%g' % d for d in diameters)))
        rows = [row for row in (x.get('r') or []) if isinstance(row, (list, tuple)) and len(row) == 3
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in row)]
        if not rows:
            return ('the census of this part stores no radii of %s on the axis of this hole (measured since 0.3.2): nothing shows that it passes with room. '
                    'Measure the part again (gate.py run), or give the label or fit parameter that is true' % _leaf(x.get('name')))
        if not segs:
            return 'the census does not give the segments of this hole: the radii of %s cannot be held against its wall' % _leaf(x.get('name'))
        for row in rows:
            r, lo, hi = row[0], min(row[1], row[2]), max(row[1], row[2])
            for R, a, b in segs:
                if min(hi, b) - max(lo, a) > ENTER_MIN and r >= R - room - 1e-9:
                    if r > R + room + 1e-9:
                        return ('%s has a cylinder of D%g on the axis of this hole where the hole is only D%g: it is wider than the hole, so it is pressed '
                                'in or collides with the wall; it does not pass with room. Name the fit parameter its size comes from, or correct the model'
                                % (_leaf(x.get('name')), round(2 * r, 3), round(2 * R, 3)))
                    return ('%s has a cylinder of D%g on the axis of this hole where the hole is D%g: within %s = %g of its wall, so it is fitted in it. '
                            'Name the fit parameter its size comes from' % (_leaf(x.get('name')), round(2 * r, 3), round(2 * R, 3), room_param, room))
    return None


def _touching_unseen(root, question, sig, hole=None):
    """Why a mesh or bought body that touches the part could be what goes in the hole of this question, or None.
    The census reads the cylinders of solid bodies only: a bought part imported as a mesh, or a bought solid with no
    cylinder on the hole's axis (a square nut, a board, a magnet block), has no "coaxial" row, so the census cannot
    show that it stays out of the hole. Refused while fusion_near lists a body of kind "mesh" or "bought" whose box
    touches the part's box (gap up to TOUCH_GAP, or no gap given) and that is no "coaxial" row of this hole (those are
    judged on their span and radii). The near unit measures the box of the whole part, not of the hole: a body that
    touches the part elsewhere is refused too (fail closed), and the answer is then a fit parameter, a waiver of the
    owner, or a measure again (gate.py run) after the body was moved off. Facts of the near unit that were never
    measured refuse nothing."""
    near = _facts_of(root, question['part'], 'fusion_near', sig)
    if not isinstance(near, dict):
        return None
    if hole is None:
        hole, _ = _question_hole(root, question, sig)
    on_axis = {str(x.get('name')) for x in (_coaxial([hole], ('bought', 'hardware', 'mesh')) if isinstance(hole, dict) else [])}
    touch = [x.get('name') for x in _rows(near, 'bodies') if x.get('kind') in ('mesh', 'bought') and str(x.get('name')) not in on_axis
             and not (isinstance(x.get('gap'), (int, float)) and not isinstance(x.get('gap'), bool) and x['gap'] > TOUCH_GAP)]
    if not touch:
        return None
    return ('%d mesh or bought body(ies) whose box touches this part and that the census does not see on the axis of this hole (%s): a mesh, or a '
            'bought solid with no cylinder on the axis, can sit in this hole and nothing shows that it does not. Name the fit parameter the hole '
            'comes from, ask the owner for a waiver, or move the body off and measure again (gate.py run)' % (len(touch), _some(_leaf(n) for n in touch)))


def _no_head_seat(root, reg, question, sig):
    """Why "a screw head seat" does not hold, or None: no screw by its own name (_standard_part) reaches into the hole
    on its axis, or the widest diameter of the hole is no seat for a head of the screws table of that thread (at least
    the head, at most its counterbore or seat diameter + 1 mm)."""
    hole, _ = _question_hole(root, question, sig)
    if not isinstance(hole, dict):
        return 'the hole of this question is not in the census of the part: no screw can be found on its axis'
    params = _fact_params(root, reg)
    segs = _hole_segments(hole)
    depth = hole.get('depth') if isinstance(hole.get('depth'), (int, float)) else (-segs[-1][1] if segs else None)
    screws = [str(x.get('name') or '') for x in _coaxial([hole]) if _standard_part(x.get('name'), params, reg.get('tables')) == 'screw' and enters(x.get('span'), depth)]
    if not screws:
        return 'no screw (a thread of the screws table by its own name) reaches into this hole on its axis: it is no head seat'
    widest = max([2 * r for r, _, _ in segs] or [0.0])
    sizes = ((reg.get('tables') or {}).get('screws') or {}).get('sizes') or {}
    threads = {m.group(0).upper() for n in screws for m in [re.search(r'(?i)\bM\d+(?:\.\d+)?', _leaf(n))] if m}
    rows = [sizes[k] for k in sizes if not threads or k.upper() in threads]
    heads = [h for row in rows if isinstance(row, dict) for h in (row.get('heads') or {}).values() if isinstance(h, dict) and isinstance(h.get('d'), (int, float))]
    if not heads:
        return None
    for h in heads:
        top = max([h['d']] + [h[k] for k in ('counterbore_d', 'seat_d') if isinstance(h.get(k), (int, float))]) + 1.0
        if h['d'] - 0.05 <= widest <= top + 1e-9:
            return None
    return ('the hole is D%g at its widest: no head of the screws table for %s seats in it (%s)'
            % (widest, ', '.join(sorted(threads)) or 'any thread', ', '.join(sorted({'D%g' % h['d'] for h in heads}))))


def _param_fits(root, reg, resolved, question, name):
    """Why this parameter is not evidence for this question, or None (the rule's attest.param narrows what counts)."""
    want = ((reg['rules'].get(question.get('rule')) or {}).get('attest') or {}).get('param') or {}
    entry = resolved[name]
    if want.get('class') and entry.get('class') != want['class']:
        return 'rule %s takes a parameter of class "%s" as evidence; %s is of class "%s"' % (question['rule'], want['class'], name, entry.get('class'))
    if want.get('matches') == 'hole_d':
        holes = (_facts_of(root, question['part'], 'fusion_census') or {}).get('holes') or []
        hole = gate_features.find(question['anchor'], [h for h in holes if isinstance(h, dict)]) if question.get('anchor') else None
        segs = hole.get('segments') if isinstance(hole, dict) else None
        try:
            d = float(segs[-1]['d'])
        except (TypeError, ValueError, KeyError, IndexError):
            return 'the hole of this question is not in the census of the part: its diameter cannot be held against %s' % name
        tol = ((reg.get('tables') or {}).get('pockets') or {}).get('tol') or {}
        tol = tol.get('d') if isinstance(tol.get('d'), (int, float)) else 0.03
        v = entry.get('value')
        if not isinstance(v, (int, float)) or isinstance(v, bool) or abs(d - v) > tol + 1e-9:
            return 'the pocket is D%g at its floor, %s is %s: the size of this pocket does not come from that parameter' % (d, name, v)
    return None


def _denied_by_facts(root, reg, question, kind, value):
    """The refusal text for an answer to a question about the whole part that says the question does not apply while
    the facts of the part show that it does (answer_contradicted), or None."""
    if question.get('finding') or (question.get('anchor') or {}).get('kind') != 'part':
        return None
    sig = (gate_common.load_census(root)['parts'].get(question['part']) or {}).get('sig')
    why = answer_contradicted(root, reg, question.get('rule'), question['part'], sig, kind, value)
    if not why:
        return None
    return ('the facts of this part show what this answer denies: %s. Take the answer that is true; when a unit did not recognise '
            'the hardware, see the row of hardware.recognised first' % why)


def validate_answer(root, reg, resolved, question, kind, value):
    """None when the evidence is good for this question, else one line why not.

    question  a question of questions_for_part()      kind  one of KINDS      value  what was given
    resolved  gate_params.resolve() of the project (for "param")
    Refused: a kind the rule does not allow; a label that is not in the list, or one the facts of the part contradict
    (label_contradicted); a label or a "not applicable" that says the question does not apply while the facts show
    that it does (answer_contradicted); a picture that does not exist or is older than the part's latest run; a parameter that is
    unknown, tagged D or S, a value of the declared target, or not what the rule's attest.param asks for; an "na"
    without a reason
    (gate_features.reason_error); a waiver id that is not valid for this rule, part and anchor."""
    if kind not in KINDS:
        return 'evidence kind must be one of %s, got %r' % (', '.join(KINDS), kind)
    if kind not in question.get('evidence', []):
        return 'rule %s takes %s as evidence, not %s' % (question['rule'], ' or '.join(question.get('evidence') or ['nothing']), kind)
    if not isinstance(value, str) or not value.strip():
        return 'the %s is empty' % kind
    if kind == 'label':
        if value not in question.get('labels', []):
            return 'the label must be one of the fixed answers, word for word: %s' % ' | '.join('"%s"' % x for x in question.get('labels', []))
        return label_contradicted(root, reg, question, value) or _denied_by_facts(root, reg, question, kind, value)
    if kind == 'shot':
        path = value if os.path.isabs(value) else os.path.join(root, value)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return 'no picture at %s' % value
        if not path.lower().endswith(PICTURES):
            return '%s is not a picture (%s)' % (value, ', '.join(PICTURES))
        census = gate_common.load_census(root)
        start = _run_start(root, question['part'], (census['parts'].get(question['part']) or {}).get('sig'))
        if start is None:
            return 'the part has no run yet: run the gate first, then take the picture'
        if os.path.getmtime(path) < start:
            return 'the picture is older than the latest run of %s: take it after the run' % question['part']
        return None
    if kind == 'param':
        entry = (resolved or {}).get(value)
        if entry is None:
            return 'unknown parameter %s' % value
        if entry.get('tag') not in ('M', 'U'):
            return 'parameter %s is tagged %s here: only a measured (M) or owner-set (U) value is evidence' % (value, entry.get('tag'))
        if entry.get('from') == 'target' or str(value).startswith('target.'):
            return 'parameter %s is a value of the declared target, not a measured size: it is no evidence' % value
        return _param_fits(root, reg, resolved, question, value)
    if kind == 'na':
        why = gate_features.reason_error(value)
        return ('"not applicable" needs a reason: ' + why) if why else _denied_by_facts(root, reg, question, kind, value)
    w = next((x for x in _owner_waivers(root) if isinstance(x, dict) and x.get('id') == value), None)
    if w is None:
        return 'no waiver %s in gate/owner/waivers.json' % value
    if _waiver_for([w], {question['rule'], question.get('finding_rule')} - {None}, question['part'], question['anchor'], question['context']) is None:
        return 'waiver %s is not a sound waiver for %s on this feature of %s in its present state' % (value, question['rule'], question['part'])
    return None


def answer(root, reg, resolved, qid, kind, value, run=None):
    """Store one answer. Looks the question up among open_questions(), checks it with validate_answer() and appends it
    to the part's attest file. Returns the stored answer. GateError when the id is unknown or the evidence is refused."""
    q = next((x for x in open_questions(root, reg, waiting=True) if x['q'] == qid), None)
    if q is None:
        raise GateError('no open question %s (python3 gate/tools/gate.py attest list shows the open ones)' % (qid,))
    why = validate_answer(root, reg, resolved, q, kind, value)
    if why:
        raise GateError('answer to %s refused: %s' % (qid, why))
    census = gate_common.load_census(root)
    stored = {'q': q['q'], 'rule': q['rule'], 'part': q['part'], 'anchor': q['anchor'], 'context': q['context'], 'kind': kind,
              'value': value, 'sig': (census['parts'].get(q['part']) or {}).get('sig'), 'time': gate_common.now(), 'run': run}
    if kind == 'shot':
        path = value if os.path.isabs(value) else os.path.join(root, value)
        stored['value'] = gate_common.rel(root, path)
        stored['sha256'] = gate_common.sha256_file(path)
    path = gate_common.attest_path(root, q['part'])
    data = read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    data['part'] = q['part']
    data['answers'] = list(data.get('answers') or []) + [stored]
    write_json(path, data)
    return stored


def attested_count(root, reg, part, sig, findings, stages=('design', 'export')):
    """How many answers the part's pass rests on at this signature: answered part-level questions plus findings closed
    by an answer. The n of pass_attested(n). An answer the gate gave itself from the facts (kind "facts") is not one."""
    return sum(1 for q in questions_for_part(root, reg, part, sig, findings, stages) if q.get('answer') and q['answer'].get('kind') != FACTS)


def _print_evidence(kinds, labels, qid='Q-ID'):
    for kind in kinds:
        if kind == 'label':
            for text in labels:
                print('      --label "%s"' % text)
        elif kind == 'shot':
            print('      --shot PATH      a picture taken after the latest run of this part')
        elif kind == 'param':
            print('      --param NAME     a parameter that is measured (M) or owner-set (U) in this project')
        elif kind == 'na':
            print('      --na "why"       the reason the question does not apply to this part')
        elif kind == 'waiver':
            print('      (or the owner waives it: python3 gate/tools/gate.py owner waive %s --reason "..." --quote "...")' % qid)


def _print_question(q):
    """One question with everything it takes, for a reader who sees only this one."""
    print('%s  %s  %s%s' % (q['q'], q['part'], q['rule'], ('  [%s]' % q['stage']) if q.get('stage') else ''))
    print('    %s' % q['question'])
    if q.get('finding'):
        print('    about the finding %s of %s' % (q['finding'], q.get('finding_rule')))
    _print_evidence(q['evidence'], q['labels'], q['q'])


def _print_questions(questions, reg):
    """The questions grouped by rule: what the rule asks and the answers it takes are printed once, then one line per
    question, "Q-id  part  rule", with what differs (the feature, the finding, what the facts of the part show)."""
    by_rule = {}
    for q in questions:
        by_rule.setdefault(q['rule'], []).append(q)
    for rid in sorted(by_rule):
        rows = by_rule[rid]
        a = (reg['rules'].get(rid) or {}).get('attest') or {}
        per_part = not rows[0].get('finding')
        print('%s%s: %d question(s)' % (rid, ('  [%s]' % rows[0]['stage']) if rows[0].get('stage') else '', len(rows)))
        print('    %s' % (rows[0]['question'] if per_part else
                          (a.get('question') or rows[0]['question']).replace('{feature}', '<where>').replace('{by}', '<what covers it>')))
        _print_evidence(rows[0]['evidence'], rows[0]['labels'])
        for q in rows:
            print('%s  %s  %s%s' % (q['q'], q['part'], q['rule'], '  (waits: an inherited part)' if q.get('waiting') else ''))
            if not per_part:
                print('    %s' % q['question'])
                print('    about the finding %s of %s' % (q['finding'], q.get('finding_rule')))
            for text in q.get('facts') or []:
                print('    the facts: %s' % text)


def cmd_attest(root, args):
    """gate.py attest list [--part ID] [--waiting] | answer Q (--label | --na | --param | --shot) [--for KEY].
    args.target (--for): the .3mf project a --param answer is judged for, when several targets are declared.
    list: prints the open questions of the current census grouped by rule: what the rule asks and the evidence it takes
    once, then one line "Q-id  part  rule" per question; returns 2 when any is open, else 0. The standing questions of
    inherited parts (see the top) are counted in one line and not listed; args.part (--part ID: the questions of one
    part) and args.waiting (--waiting) list them too.
    answer: args.question is the id; exactly one of args.label, args.na, args.param, args.shot is set. Stores the answer
    when validate_answer() accepts it and returns 0 (or 2 when other questions stay open); prints why and returns 4 when
    it is refused or the id is unknown."""
    import gate_params
    reg = gate_registry.load_default(root)
    if args.action == 'list':
        only = getattr(args, 'part', None)
        every = open_questions(root, reg, parts=[only] if only else None, waiting=True)
        left = [q for q in every if not q.get('waiting')]
        waiting = [q for q in every if q.get('waiting')]
        _print_questions(every if (only or getattr(args, 'waiting', False)) else left, reg)
        print('%d question(s) open' % len(left))
        if waiting:
            parts = sorted({q['part'] for q in waiting})
            print('%d question(s) of %d inherited part(s) wait: they are asked when the part changes, and at its export. '
                  '`attest list --part ID` shows them%s' % (len(waiting), len(parts), '' if only else ' (%s)' % _some(parts, 4)))
        if left:
            print('answer one at a time: python3 gate/tools/gate.py attest answer Q-ID --label "..." (the label word for word)')
        return EXIT_ATTEST if left else EXIT_PASS
    given = [(k, getattr(args, k)) for k in ('label', 'na', 'param', 'shot') if getattr(args, k, None) is not None]
    if not args.question or len(given) != 1:
        raise GateError('attest answer needs a question id and exactly one of --label, --na, --param, --shot')
    project = gate_common.load_project(root)
    key = getattr(args, 'target', None)
    if key is None and given[0][0] == 'param' and len(project.get('targets') or {}) > 1:
        raise GateError('gate/project.json declares several targets and a parameter is measured (M) for one of them: name the .3mf project '
                        'with --for KEY (%s)' % ', '.join(sorted(project['targets'])))
    resolved = gate_params.resolve(reg, gate_params.load_overrides(root), gate_common.target_of(project, key))
    plan = read_json(gate_common.gate_path(root, 'run', 'current.json'), None) or {}
    stored = answer(root, reg, resolved, args.question, given[0][0], given[0][1], run=plan.get('run'))
    print('answered %s (%s) for %s: %s' % (stored['q'], stored['kind'], stored['part'], stored['value']))
    left = open_questions(root, reg)
    print('%d question(s) open' % len(left))
    return EXIT_ATTEST if left else EXIT_PASS
