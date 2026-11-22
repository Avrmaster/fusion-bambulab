"""Reports: what a run found (json + md), the status without Fusion, the session-start digest, the owner's triage list.

A report, gate/reports/<run>.json (latest.json is a copy of the newest; <run>.md is rendered from it):

    {"run": "20310203T101207Z-3fa2", "command": "run" | "export" | "verify", "stage": "design" | "export" | "project",
     "time": "...", "mode": "observe" | "enforce", "audit": false, "intent": [part ids] or null,
     "parts": [{"part", "sig12", "state": "new" | "changed" | "unchanged" | "unreachable" | "gone",
                "verdict": "pass" | "pass_attested" | "fail" | "attest" | "incomplete" | "unreachable",
                "units": {"done": 5, "total": 6}, "open": 1, "asked": 0, "waived": 0, "not_run": ["walls.thin_faces"]}],
     "findings": [finding + {"id": "F-9c1e04", "state": "open" | "asked" | "answered" | "waived" | "inherited" | "info",
                             "waiver": id or null, "limit_text": "4.5 mm [U owner 2031-02-03]"}],
     "questions": [questions of gate_attest, answered and open],
     "waivers": [{"waiver", "rule", "part", "state": "valid" | "expired" | "orphan" | "invalid"}],
     "deferred": [ids of the rules that are registered and not built], "unreachable": ["demo2: document not open"],
     "params": [{"name", "value", "tag", "from"}]      every parameter a judge of this run read
     "totals": {"block_open": 1, "attest_open": 0, "waived": 0, "inherited": 0, "not_run": 1, "info": 3,
                "by_verdict": {"FAIL": 0, "BLOCKED": 0, "GROW": 1, "NOT_RUN": 0}    the open rows by verdict
                "by_facts": 2      questions the gate answered itself from the facts of the part
                "waiting": 0},     standing questions of inherited parts that are not asked yet
     "groups": [{"id": "G-5d0c1a", "part", "rule", "verdict", "state", "cause", "by": [names], "count": 6,
                 "findings": [finding ids]}],     equal rows of one part, see "Groups" below
     "over_budget": [{"unit", "probe", "part", "call", "elapsed_s", "budget_s", "over_s", "late"} |
                     {"unit", "probe", "part", "call", "budget_s", "no_answer": true, "waited_s", "why", "late": false} |
                     {"unit", "probe", "part", "budget_s", "not_run": true, "timeouts"}],
                    the calls of this run that worked longer than their unit's budget, the calls that got no answer
                    and left no result, and the units that were given up
                    (gate_plan.over_budget): a defect of a probe to report, one line each in the report and on screen
     "exit": 1}

Groups. Many rows on a patterned feature are one line for the reader: rows of one part with the same rule, verdict,
state, cause and neighbours are one group. The cause is the text of the finding without its numbers (cause_of), the
neighbours are the names in "by" without their copy numbers. Every finding keeps its own row and its id in the JSON
report (a waiver is per feature); the markdown report, the lines `gate.py run` prints and the triage list show the
groups. The id of a group is made from rule, verdict and cause, so the same kind of finding has the same id on every
part: `gate.py owner decide G-5d0c1a ...` decides every undecided row of that kind.

The triage list (gate/owner/triage.json, written by `gate.py owner decide` only; the agent's proposal of it is
gate/proposals/triage.json): findings of the first `run --audit`, inherited from before the gate existed.
    {"adopted": {"run", "time"}, "items": [{"id": "T-0001", "finding": "F-9c1e04", "rule", "part", "anchor", "context",
     "verdict", "measure", "limit", "text", "group": "inserts.wall demo/motor_plate", "decision": null | "fix" | "waive" |
     "tolerate" | "rule_wrong", "quote": null, "note": null, "date": null}]}
An inherited finding with no decision blocks only the next export of its own part.
The list also holds "baseline": {part id: {"sig", "run", "time"}}, the geometry each part had when the audit judged it,
and the proposal holds "not_run": the checks that could not decide in the audit. Those are not items: a check that did
not run cannot be tolerated or waived, it stays open until it runs.

Inherited parts. A part is INHERITED while its census signature is the one in the baseline (the owner's list once he
adopted the audit, the proposal before that) and it has no stamp. A proposal entry counts only while the report of its
audit stands behind it, and only until the owner has a list: from then on a later audit only PROPOSES new parts (their
rows are ordinary open rows and go into the proposal), and `gate.py owner adopt` is what makes them inherited. So a
part that is new after the adoption is never born inherited. For an inherited part
  - its findings are items of the triage list (a finding that carries a question about a feature is not: a question
    is answered, not triaged);
  - its questions, the standing ones of the part (the part-level attest rules) and the ones about a feature, are not
    asked by run, status and attest list: nothing about the part changed. They are listed as waiting;
  - it has no stamp, and it gets none without them: `gate.py export` asks every question and holds every undecided
    row, exactly as for any other part.
It stops being inherited when (1) its geometry changes: the next scan gives it another signature, it is a changed part,
every standing question is asked, and a feature that changed gives new findings; or (2) it is exported and stamped.
The baseline of a part is written once, by the first audit that judges it. A later audit does not move it, so a part
that changed after the audit cannot be made "inherited" again by running the audit a second time. A carried mesh of an
inherited part in a .3mf project is never judged by the writer's guard, in observe and in enforce mode (gate_guard): it
was in the file before; only a NEW mesh needs a stamp.

Inherited against new. A finding of today is INHERITED when gate/owner/triage.json holds an item with the same rule and
part, a matching anchor and the same context: it is listed, it does not count as open, and inherited_block() says
whether it stops the export of its part. Any other finding is NEW: an ordinary block. A feature that changed since the
audit has another context, so it is new again."""
import json, os, re, time
import gate_common, gate_features, gate_params, gate_registry
from gate_common import GateError, EXIT_PASS, EXIT_BLOCK, EXIT_ATTEST, EXIT_MORE, EXIT_INFRA, gate_path, read_json, write_json

DECISIONS = ('fix', 'waive', 'tolerate', 'rule_wrong')
BLOCKING_DECISIONS = (None, 'fix')              # an item with one of these stops the next export of its part
DIGEST_LINES = 15
VERDICT_ORDER = ('FAIL', 'BLOCKED', 'GROW', 'NOT_RUN', 'INFO')      # how rows are sorted: what is measured and wrong first
PLACES_SHOWN = 6                # places of a group the markdown report names; the JSON report holds every id
RUN_LINES = 12                  # group lines `gate.py run` and `gate.py status` print before they point at the report
LINE_TEXT = 240                 # characters of a finding's text in such a line; the report holds the whole text
COMMANDS_LINE = ('python3 gate/tools/gate.py status   |   python3 gate/tools/gate.py run --changed   |   '
                 'python3 gate/tools/gate.py export PART')
CHOICES = (('fix', 'change the model before the next export of this part (the export stays blocked until the row is gone)'),
           ('waive', 'the feature may stay as it is; say why, then `gate.py owner waive` records it for this feature in this state'),
           ('tolerate', 'leave it in this print; it stays listed and is looked at again when the feature changes'),
           ('rule_wrong', 'the rule or its number is wrong for this project: the rule or the parameter gets changed, not the part'))


# ---- triage ----------------------------------------------------------------------------------------------------------------

def triage_of(root, part=None):
    """The items of gate/owner/triage.json (of one part, or all). [] when the owner adopted no list yet."""
    data = read_json(gate_path(root, 'owner', 'triage.json'), {})
    items = [i for i in (data.get('items') or []) if isinstance(i, dict)] if isinstance(data, dict) else []
    return [i for i in items if part is None or i.get('part') == part]


def triage_match(items, finding):
    """The triage item this finding is: same rule and part, a matching anchor (gate_features.anchors_match) and the same
    context. None = the finding is new."""
    for i in items or []:
        if (i.get('rule') == finding.get('rule') and i.get('part') == finding.get('part') and i.get('context') == finding.get('context')
                and gate_features.anchors_match(i.get('anchor'), finding.get('anchor'))):
            return i
    return None


def _proposal(root):
    """The "data" object of gate/proposals/triage.json, or {}."""
    prop = read_json(gate_path(root, 'proposals', 'triage.json'), {})
    data = prop.get('data') if isinstance(prop, dict) else None
    return data if isinstance(data, dict) else {}


_AUDITS_HERE = set()            # (project root, run id) of the audits this process has noted a part for (baseline_add)
_AUDIT_PARTS = {}               # report path -> (mtime, size, {(part, sig12)}) of an audit report read by _audit_holds


def owner_adopted(root):
    """Has the owner taken an audit over: gate/owner/triage.json holds "adopted", a baseline or items."""
    owner = read_json(gate_path(root, 'owner', 'triage.json'), {})
    return isinstance(owner, dict) and bool(owner.get('adopted') or owner.get('baseline') or owner.get('items'))


def _audit_holds(root, run, part, sig):
    """Did the audit `run` judge this part at this signature? True when its report (gate/reports/<run>.json, with
    "audit" true) lists the part with that signature, and for the audit this process is running right now (its report
    is written at the end of the call). An entry of the proposal that no audit report stands behind is no baseline: the
    proposal is a file an agent can write."""
    if not run or not sig:
        return False
    if (os.path.abspath(root), run) in _AUDITS_HERE:
        return True
    path = gate_path(root, 'reports', '%s.json' % run)
    try:
        st = os.stat(path)
    except OSError:
        return False
    hit = _AUDIT_PARTS.get(path)
    if hit is None or hit[:2] != (st.st_mtime, st.st_size):
        try:
            report = read_json(path, {})
        except GateError:
            report = {}
        rows = set()
        if isinstance(report, dict) and report.get('audit') is True:
            rows = {(p.get('part'), p.get('sig12')) for p in report.get('parts') or [] if isinstance(p, dict)}
        if len(_AUDIT_PARTS) > 50:
            _AUDIT_PARTS.clear()
        hit = _AUDIT_PARTS[path] = (st.st_mtime, st.st_size, rows)
    return (part, gate_common.sig12(sig)) in hit[2]


def baseline(root):
    """{part id: {"sig", "run", "time", "adopted": bool, "live": bool}}: the geometry each part had when an audit judged
    it. An entry of the owner's list (gate/owner/triage.json "baseline") wins over one of the proposal.
    "live": the entry makes the part an inherited part. An entry of the owner's list always is. An entry of the
    proposal is live only while the owner has adopted nothing yet AND the report of its audit lists the part with that
    signature (_audit_holds). Once the owner's list exists, only `gate.py owner adopt` adds to what is inherited: a
    later audit proposes, and a part it proposes is an ordinary part until the owner takes it over."""
    out = {}
    owner = read_json(gate_path(root, 'owner', 'triage.json'), {})
    owner = owner if isinstance(owner, dict) else {}
    has_list = bool(owner.get('adopted') or owner.get('baseline') or owner.get('items'))
    for data, adopted in ((_proposal(root), False), (owner, True)):
        for pid, e in sorted((data.get('baseline') or {}).items()) if isinstance(data.get('baseline'), dict) else []:
            if isinstance(e, dict) and e.get('sig'):
                out[pid] = dict(e, adopted=adopted, live=adopted or (not has_list and _audit_holds(root, e.get('run'), pid, e['sig'])))
    return out


def is_inherited(root, part, sig, base=None):
    """Is this the geometry the audit judged: the part is in the baseline with exactly this signature, and that entry
    is live (baseline()). (The caller adds "and it has no stamp": see the top.)"""
    e = (base if base is not None else baseline(root)).get(part)
    return bool(sig) and isinstance(e, dict) and e.get('sig') == sig and e.get('live', True) is not False


def is_proposed(root, part, sig, base=None):
    """Is this geometry only PROPOSED as inherited: an audit judged it after the owner adopted his list, and he has not
    taken that audit over. Such a part is judged as in any run; the audit writes its rows into the proposal, and
    `gate.py owner adopt` makes them inherited."""
    e = (base if base is not None else baseline(root)).get(part)
    return bool(sig) and isinstance(e, dict) and e.get('sig') == sig and not e.get('adopted') and e.get('live') is False and owner_adopted(root)


def _has_stamp(root, part):
    doc, name = gate_common.split_part_id(part)
    folder = gate_path(root, 'stamps', doc, name)
    return os.path.isdir(folder) and any(n.endswith('.json') for n in os.listdir(folder))


def baseline_add(root, run, part, sig):
    """Note in the proposal that an audit judged this part at this signature. Written once: a part that is in a baseline
    already (the owner's, or a live entry of the proposal), or that has a stamp of any geometry, is left as it is.
    An entry of the proposal that is not live (no audit report stands behind it, or it was only proposed after the
    owner adopted his list) made nothing inherited yet: it is written again for the geometry this audit judged.
    Returns True when the entry was written."""
    have = baseline(root).get(part)
    if not sig or _has_stamp(root, part) or (have is not None and (have.get('adopted') or have.get('live'))):
        return False
    _AUDITS_HERE.add((os.path.abspath(root), run))
    path = gate_path(root, 'proposals', 'triage.json')
    prop = read_json(path, {})
    prop = prop if isinstance(prop, dict) else {}
    data = prop.get('data') if isinstance(prop.get('data'), dict) else {}
    data.setdefault('adopted', {'run': run, 'time': gate_common.now()})
    data.setdefault('items', [])
    data['baseline'] = dict(data.get('baseline') or {}, **{part: {'sig': sig, 'run': run, 'time': gate_common.now()}})
    write_json(path, dict(prop, kind='triage', data=data))
    return True


_NUMBER = re.compile(r'[-+]?\d+(?:\.\d+)?')
_COPY = re.compile(r'\s*(#\s*\d+|\(\d+\))\s*$')


def cause_of(text):
    """The cause of a finding: its text without the numbers ("wall 2.22 at (4, 0, 7)" and "wall 2.4 at (9, 0, 7)" are one
    cause). Names stay, so two different neighbours are two causes."""
    return re.sub(r'\s+', ' ', _NUMBER.sub('#', str(text or ''))).strip()


def neighbours_of(by):
    """The names of what stops a fix, copy numbers dropped ("screw #3" and "screw #4" are one neighbour), sorted."""
    return sorted({_COPY.sub('', str(n)) for n in by or []})


def group_id(rule, verdict, cause):
    """'G-5d0c1a': the same on every part for the same rule, verdict and cause."""
    return 'G-' + gate_common.digest({'rule': rule, 'verdict': verdict, 'cause': cause}, 6)


def _verdict_rank(verdict):
    return VERDICT_ORDER.index(verdict) if verdict in VERDICT_ORDER else len(VERDICT_ORDER)


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def group_rows(rows, per_part=True, neighbours=True):
    """Rows (findings of a report, or triage items) as groups, blockers first: same rule, verdict, cause, and (unless
    switched off) part and neighbours. Returns [{"id", "rule", "verdict", "cause", "by", "part" (None across parts),
    "rows": [...] sorted by measure and id, "count"}] sorted by verdict (VERDICT_ORDER), rule, then the larger group."""
    out = {}
    for r in rows or []:
        cause = r.get('cause') or cause_of(r.get('text'))
        by = neighbours_of(r.get('by')) if neighbours else []
        key = (r.get('rule') or '', r.get('verdict') or '', cause, tuple(by), (r.get('of') or r.get('part') or '') if per_part else '')
        g = out.setdefault(key, {'id': group_id(key[0], key[1], cause), 'rule': key[0], 'verdict': key[1], 'cause': cause, 'by': by,
                                 'part': key[4] or None, 'rows': []})
        g['rows'].append(r)
    groups = list(out.values())
    for g in groups:
        g['rows'].sort(key=lambda r: (_num(r.get('measure')) is None, _num(r.get('measure')) or 0, r.get('id') or ''))
        g['count'] = len(g['rows'])
    groups.sort(key=lambda g: (_verdict_rank(g['verdict']), g['rule'], -g['count'], g['cause'], g['part'] or ''))
    return groups


def _measure_range(rows):
    """'2.22 mm' or '2.22 .. 3.1 mm' over the rows that carry a number; None when none does."""
    nums = [r for r in rows if _num(r.get('measure')) is not None]
    if not nums:
        return None
    lo, hi = min(r['measure'] for r in nums), max(r['measure'] for r in nums)
    unit = next((r.get('unit') for r in nums if r.get('unit')), '') or ''
    return ('%s %s' % (lo if lo == hi else '%s .. %s' % (lo, hi), unit)).strip()


def _stored_findings(root, part):
    """Every finding stored for the part's current signature, whatever its stage."""
    entry = gate_common.load_census(root)['parts'].get(part) or {}
    if not entry.get('sig'):
        return None
    judged = gate_common.load_facts(root, part, entry['sig']).get('judged') or {}
    if not judged:
        return None
    return [f for j in judged.values() for f in (j.get('findings') or [])]


def inherited_block(root, part):
    """Triage items of this part that have no decision, or the decision "fix": they block its next export.
    Returns a list of items ([] = nothing inherited stands in the way).

    An item whose finding is gone from the part's current judgement (the feature was fixed, or it changed and is a new
    finding now) no longer blocks. A part whose current geometry was never judged keeps every such item."""
    items = [i for i in triage_of(root, part) if i.get('decision') in BLOCKING_DECISIONS]
    if not items:
        return []
    found = _stored_findings(root, part)
    if found is None:
        return items
    return [i for i in items if any(triage_match([i], f) is not None for f in found)]


def _limit_text(reg, resolved, finding):
    limit = finding.get('limit')
    if limit is None:
        return None
    rule = (reg.get('rules') or {}).get(finding.get('rule')) or {}
    for name in rule.get('params') or []:
        entry = (resolved or {}).get(name)
        if not entry:
            continue
        if entry.get('value') == limit:
            return gate_params.limit_text(entry)
        for key, value in sorted((entry.get('per') or {}).items()):
            if value == limit:
                return '%s (%s)' % (gate_params.limit_text(entry, key), key)
    unit = finding.get('unit') or ''
    return ('%s %s' % (limit, unit)).strip()


def triage_items(report, existing=None, previous=None):
    """The triage items for the findings of an audit report, numbered T-0001.. `existing` (the items of
    gate/owner/triage.json, and the items of an earlier proposal for parts this report does not hold) is kept as it is,
    decisions and all: same finding id = same item. `previous` (the items an earlier proposal held for the parts of this
    report) only gives a finding its old number back, so a second call of one audit does not renumber the list.

    Only rows with the state "inherited" become items (build() gives it to the open rows of inherited parts), and the
    open rows marked "proposed" (a part an audit judged after the owner adopted his list: its rows stay open, and the
    proposal offers them to the owner). A row that could not decide (NOT_RUN) is no item: it cannot be tolerated, it
    stays open until it runs.
    Besides the keys at the top an item carries "unit", "limit_text", "by" (what stops the fix), "fix" (the rule's fix
    order), "cause" and "gid" (the group, see the top), so the list can be read without the report."""
    items = [dict(i) for i in (existing or []) if isinstance(i, dict)]
    have = {i.get('finding') for i in items}
    taken = {i.get('id') for i in items}
    old_id = {i.get('finding'): i.get('id') for i in (previous or []) if isinstance(i, dict) and i.get('id') not in taken}
    n = 0
    for i in items + [i for i in (previous or []) if isinstance(i, dict)]:
        try:
            n = max(n, int(str(i.get('id', 'T-0')).split('-')[1]))
        except (IndexError, ValueError):
            pass
    fresh = [f for f in report.get('findings') or [] if (f.get('state') == 'inherited' or (f.get('state') == 'open' and f.get('proposed')))
             and f.get('verdict') != 'NOT_RUN' and f.get('id') not in have]
    fresh.sort(key=lambda f: (f.get('rule') or '', f.get('part') or '', f.get('measure') if isinstance(f.get('measure'), (int, float)) else 0, f.get('id') or ''))
    for f in fresh:
        tid = old_id.get(f.get('id'))
        if tid is None or tid in taken:
            n += 1
            tid = 'T-%04d' % n
        taken.add(tid)
        have.add(f.get('id'))
        cause = f.get('cause') or cause_of(f.get('text'))
        items.append({'id': tid, 'finding': f.get('id'), 'rule': f.get('rule'), 'part': f.get('part'), 'anchor': f.get('anchor'),
                      'context': f.get('context'), 'verdict': f.get('verdict'), 'measure': f.get('measure'), 'limit': f.get('limit'),
                      'unit': f.get('unit'), 'limit_text': f.get('limit_text'), 'text': f.get('text'), 'by': list(f.get('by') or []),
                      'fix': list(f.get('fix') or []), 'group': '%s %s' % (f.get('rule'), f.get('part')), 'cause': cause,
                      'gid': group_id(f.get('rule') or '', f.get('verdict') or '', cause), 'ask': f.get('ask'),
                      'decision': None, 'quote': None, 'note': None, 'date': None})
    return items


def item_gid(item):
    """The group id of a triage item (made here for an item written before groups existed)."""
    return item.get('gid') or group_id(item.get('rule') or '', item.get('verdict') or '', item.get('cause') or cause_of(item.get('text')))


def write_proposal(root, report):
    """After an audit call: write gate/proposals/triage.json from the report. The owner's items stay first, with their
    decisions. Items an earlier audit proposed for parts that are NOT in this report are kept; the parts of this report
    get their items from it. The same holds for "not_run" (the checks that could not decide: rows with the verdict
    NOT_RUN and rules with nothing stored). The baseline is kept as it is (baseline_add writes it).
    Returns the items."""
    path = gate_path(root, 'proposals', 'triage.json')
    old, run = _proposal(root), report.get('run')
    here = {p.get('part') for p in report.get('parts') or []}
    owner = (read_json(gate_path(root, 'owner', 'triage.json'), {}) or {}).get('items') or []
    owned = {i.get('finding') for i in owner if isinstance(i, dict)}
    earlier = [i for i in old.get('items') or [] if isinstance(i, dict) and i.get('finding') not in owned]
    items = triage_items(report, list(owner) + [i for i in earlier if i.get('part') not in here], [i for i in earlier if i.get('part') in here])
    items.sort(key=lambda i: (len(str(i.get('id'))), str(i.get('id'))))              # by number, whichever audit call brought the row
    not_run = [r for r in old.get('not_run') or [] if isinstance(r, dict) and r.get('part') not in here]
    for f in report.get('findings') or []:
        if f.get('verdict') == 'NOT_RUN' and f.get('state') == 'open':
            not_run.append({'part': f.get('part'), 'rule': f.get('rule'), 'text': f.get('text'), 'finding': f.get('id')})
    for p_ in report.get('parts') or []:
        for rid in p_.get('not_run') or []:
            not_run.append({'part': p_.get('part'), 'rule': rid, 'finding': None,
                            'text': 'nothing is stored for this check: its unit did not finish. `gate.py run --part %s` runs it again' % p_.get('part')})
    data = {'adopted': old.get('adopted') or {'run': run, 'time': gate_common.now()}, 'items': items, 'not_run': not_run,
            'baseline': old.get('baseline') or {}, 'last_run': run}
    write_json(path, {'kind': 'triage', 'time': gate_common.now(),
                      'why': 'findings of `gate.py run --audit`: inherited from before the gate. The owner adopts the list with '
                             '`gate.py owner adopt`, or with his first `gate.py owner decide`.', 'data': data})
    return items


def _where(anchor):
    if not anchor or anchor.get('kind') == 'part':
        return 'whole part'
    return '%s at (%s), axis (%s)' % (anchor.get('kind'), ', '.join('%g' % c for c in anchor.get('point') or []),
                                      ', '.join('%g' % c for c in anchor.get('axis') or []))


def _measure(f):
    if f.get('measure') is None:
        return '-'
    return ('%s %s' % (f['measure'], f.get('unit') or '')).strip()


def _cell(text):
    return str(text if text is not None else '-').replace('|', '/').replace('\n', ' ')


def _places(rows, n=PLACES_SHOWN, key='id'):
    """'at (4, 0, 7) F-9c1e04 2.22 mm; ...' for the first n rows, and how many more there are."""
    bits = []
    for r in rows[:n]:
        a = r.get('anchor')
        where = 'whole part' if not a or a.get('kind') == 'part' else 'at (%s)' % ', '.join('%g' % c for c in a.get('point') or [])
        bits.append(' '.join(x for x in (where, str(r.get(key) or ''), _measure(r) if r.get('measure') is not None else '') if x))
    more = len(rows) - len(bits)
    return '; '.join(bits) + ('; and %d more' % more if more > 0 else '')


def _decisions(rows):
    """'4 OPEN, 2 tolerate' for the rows of a group."""
    count = {}
    for r in rows:
        count[r.get('decision') or 'OPEN'] = count.get(r.get('decision') or 'OPEN', 0) + 1
    return ', '.join('%d %s' % (count[k], k) for k in sorted(count, key=lambda k: (k != 'OPEN', k)))


def _triage_groups(items):
    """The items as groups over all parts: same rule, verdict and cause."""
    groups = group_rows(items, per_part=False, neighbours=False)
    for g in groups:
        g['id'] = item_gid(g['rows'][0])
    return groups


def render_triage_md(items, not_run=None, base=None):
    """The triage list as markdown for the owner.

    First the groups, blockers first: one line per kind of finding (rule + cause) with its count, its parts, the measured
    range, the limit with its tag and how many rows are not decided. That table is the list to read. Then the same rows
    by part: per group the places and the ids and what stops the fix. Then the fix order of each rule, once. Then the
    checks that could not decide (`not_run`: [{"part", "rule", "text"}]); those are no triage rows. `base` is baseline()."""
    items = list(items or [])
    undecided = [i for i in items if not i.get('decision')]
    groups = _triage_groups(items)
    out = ['# Triage: %d inherited findings in %d groups, %d not decided' % (len(items), len(groups), len(undecided)), '',
           'Each row was found in geometry that existed before the gate. A row with no decision, or with `fix`, stops the next '
           'export of its own part and nothing else.', '', 'The four choices:', '']
    out += ['- `%s`: %s' % c for c in CHOICES]
    out += ['', 'To decide one row: `python3 gate/tools/gate.py owner decide T-0001 fix|waive|tolerate|rule_wrong --quote "<the owner\'s words>"`', '',
            'To decide every undecided row of one group: `python3 gate/tools/gate.py owner decide G-5d0c1a tolerate --quote "..."` '
            '(add `--part ID` for one part only).', '']
    if base:
        out += ['%d parts are inherited: judged by the audit and not changed since. They have no stamp. Their standing questions are not asked '
                'until a part changes or is exported; the export asks them.' % len(base), '']
    if not items:
        out += ['No rows.', '']
    else:
        out += ['## Groups, blockers first', '', '| group | rule | verdict | rows | decisions | parts | measured | limit | what it says (one row) |',
                '|---|---|---|---|---|---|---|---|---|']
        for g in groups:
            parts = {}
            for i in g['rows']:
                parts[i.get('part') or ''] = parts.get(i.get('part') or '', 0) + 1
            out.append('| %s | %s | %s | %d | %s | %s | %s | %s | %s |' % (
                g['id'], g['rule'], g['verdict'], g['count'], _decisions(g['rows']),
                _cell(', '.join('%s (%d)' % (p, parts[p]) for p in sorted(parts))), _cell(_measure_range(g['rows']) or '-'),
                _cell(next((i.get('limit_text') for i in g['rows'] if i.get('limit_text')), '-')), _cell(g['rows'][0].get('text'))))
        out += ['', '## By part', '']
        for part in sorted({i.get('part') or '' for i in items}):
            mine = [i for i in items if (i.get('part') or '') == part]
            left = sum(1 for i in mine if i.get('decision') in BLOCKING_DECISIONS)
            out += ['### %s: %d rows, %d stop its next export' % (part, len(mine), left), '']
            for rule in sorted({i.get('rule') or '' for i in mine}, key=lambda r: (min(_verdict_rank(i.get('verdict')) for i in mine if (i.get('rule') or '') == r), r)):
                rows = [i for i in mine if (i.get('rule') or '') == rule]
                limit = next((i.get('limit_text') for i in rows if i.get('limit_text')), None)
                out += ['**%s**: %d rows%s' % (rule, len(rows), (', limit %s' % limit) if limit else ''), '']
                for g in group_rows(rows, per_part=False):
                    g_rows = sorted(g['rows'], key=lambda i: (bool(i.get('decision')), _num(i.get('measure')) is None, _num(i.get('measure')) or 0, i.get('id') or ''))
                    head = '- %s%s %s (%s)' % (g['verdict'], (' x%d' % g['count']) if g['count'] > 1 else '', item_gid(g_rows[0]), _decisions(g_rows))
                    rng = _measure_range(g_rows)
                    out.append(head + ((': %s' % rng) if rng else ''))
                    out.append('  - %s' % _cell(g_rows[0].get('text')))
                    out.append('  - stopped by: %s' % _cell(', '.join(g['by']) or 'nothing: free to fix'))
                    out.append('  - rows: %s' % _cell(_places(g_rows)))
                quotes = [i for i in rows if i.get('quote')]
                if quotes:
                    said = {}
                    for i in quotes:
                        said.setdefault((i.get('decision'), i.get('date'), i.get('quote'), i.get('note')), []).append(i.get('id'))
                    out += [''] + ['- %s decided %s on %s: "%s"%s' % (_ids(ids), k[0], k[1], k[2], (' (%s)' % k[3]) if k[3] else '') for k, ids in sorted(said.items(), key=lambda kv: kv[1])]
                out.append('')
        fixes = {}
        for i in items:
            if i.get('fix') and (i.get('rule') or '') not in fixes:
                fixes[i.get('rule') or ''] = i['fix']
        if fixes:
            out += ['## Fix orders', '', 'For a row with the decision `fix`: take step 1 unless something stops it, then step 2, and so on.', '']
            for rule in sorted(fixes):
                out += ['**%s**' % rule, ''] + ['%d. %s' % (k + 1, step) for k, step in enumerate(fixes[rule])] + ['']
    if not_run:
        by = {}
        for r in not_run:
            by.setdefault((r.get('rule') or '', cause_of(r.get('text'))), []).append(r)
        out += ['## Checks that could not decide: %d' % len(not_run), '',
                'These are not triage rows: a check that did not run has not passed, and it cannot be tolerated. Each line says what makes it run.', '']
        for (rule, _), rows in sorted(by.items()):
            out.append('- **%s** x%d: %s' % (rule, len(rows), _cell(rows[0].get('text'))))
            out.append('  - parts: %s' % _cell(', '.join(sorted({r.get('part') or '' for r in rows}))))
        out.append('')
    return '\n'.join(out)


def _ids(ids, n=6):
    ids = sorted(str(i) for i in ids)
    return ', '.join(ids[:n]) + (' and %d more' % (len(ids) - n) if len(ids) > n else '')


def _triage_source(root):
    """(items, where they come from, the audit): the owner's list when he adopted one, else the proposal of the last audit."""
    owner = read_json(gate_path(root, 'owner', 'triage.json'), {})
    if isinstance(owner, dict) and owner.get('items'):
        return list(owner['items']), 'adopted', owner.get('adopted') or {}
    data = _proposal(root)
    if data.get('items') or data.get('baseline') or data.get('not_run'):
        return list(data.get('items') or []), 'proposal', data.get('adopted') or {}
    return [], None, {}


def not_adopted(root):
    """What the newest proposal holds that the owner's list does not: (items, {part: baseline entry})."""
    owner = read_json(gate_path(root, 'owner', 'triage.json'), {})
    owner = owner if isinstance(owner, dict) else {}
    data = _proposal(root)
    have = {i.get('finding') for i in owner.get('items') or [] if isinstance(i, dict)}
    items = [i for i in data.get('items') or [] if isinstance(i, dict) and i.get('finding') not in have]
    base = {p: e for p, e in (data.get('baseline') or {}).items() if p not in (owner.get('baseline') or {})}
    return items, base


def cmd_triage(root, args):
    """gate.py triage [--md]. Prints the inherited findings as groups (rule + cause over all parts), blockers first, each
    with its count, its parts and its decisions; args.md: the whole list as markdown for the owner (render_triage_md).
    Returns 0 when every item is decided, 1 otherwise.

    After the inherited rows it lists the checks that could not decide in the audit, and what is NEW since the audit:
    the open findings of the latest report, which are ordinary blocks and no part of the triage list."""
    items, origin, adopted = _triage_source(root)
    latest = load_latest(root) or {}
    new = [f for f in latest.get('findings') or [] if f.get('state') == 'open' and not latest.get('audit')]
    not_run = _proposal(root).get('not_run') or []
    more_items, more_base = not_adopted(root) if origin == 'adopted' else ([], {})
    if origin is None:
        print('no triage list: `python3 gate/tools/gate.py run --audit` makes the proposal gate/proposals/triage.json')
    elif args.md:
        print(render_triage_md(items, not_run, baseline(root)))
        if origin == 'proposal':
            print('This is the proposal of the audit %s. It becomes the list of inherited findings with `gate.py owner adopt`, or with the '
                  'owner\'s first `gate.py owner decide`.\n' % adopted.get('run'))
    else:
        groups = _triage_groups(items)
        print('triage (%s, audit %s): %d rows in %d groups, %d not decided' % (origin, adopted.get('run'), len(items), len(groups),
                                                                             sum(1 for i in items if not i.get('decision'))))
        if origin == 'proposal':
            print('  a proposal: nothing is inherited until `gate.py owner adopt` or the owner\'s first `gate.py owner decide`')
        for g in groups:
            parts = sorted({i.get('part') or '' for i in g['rows']})
            print('  %s  %-7s %s  x%d (%s)  %s  %s  [%s]' % (g['id'], g['verdict'], g['rule'], g['count'], _decisions(g['rows']),
                                                          _measure_range(g['rows']) or '-', g['rows'][0].get('text'),
                                                          '%d parts' % len(parts) if len(parts) > 1 else parts[0]))
        if not_run:
            print('  %d check(s) could not decide in the audit: open, not triage rows (`gate.py triage --md` lists what makes each run)' % len(not_run))
        print('  the rows of a group, the fix orders and the ids: python3 gate/tools/gate.py triage --md')
    if more_items or more_base:
        print('a later audit proposes %d more row(s) and %d more part(s) that the owner has not adopted: `gate.py owner adopt --quote "..."`'
              % (len(more_items), len(more_base)))
    if new:
        groups = group_rows(new)
        if args.md:
            print('## New since the audit: %d open findings (ordinary blocks, not triage)\n' % len(new))
            print('| part | rule | verdict | rows | measured | limit | what it says (one row) | ids |\n|---|---|---|---|---|---|---|---|')
            for g in groups:
                print('| %s | %s | %s | %d | %s | %s | %s | %s |' % (g['part'], g['rule'], g['verdict'], g['count'], _measure_range(g['rows']) or '-',
                                                                  _cell(g['rows'][0].get('limit_text')), _cell(g['rows'][0].get('text')),
                                                                  _ids([f.get('id') for f in g['rows']])))
        else:
            print('new since the audit (run %s): %d open findings, ordinary blocks' % (latest.get('run'), len(new)))
            for line in group_lines(groups):
                print(line)
    return EXIT_BLOCK if any(not i.get('decision') for i in items) else EXIT_PASS


def _world_text(f):
    """' (in the document: (x, y, z))' for a row whose part is placed off the document origin (gate_plan._with_world):
    the coordinates of a text are in the part's own frame. '' otherwise."""
    w = f.get('world')
    if isinstance(w, (list, tuple)) and len(w) == 3 and all(isinstance(c, (int, float)) for c in w):
        return ' (in the document: (%s))' % ', '.join('%g' % c for c in w)
    return ''


def group_lines(groups, limit=None, word=None):
    """One printed line per group: '  FAIL    part  rule  text  [F-id]' for a single row (as a finding was always
    printed), with ' x12' and '[F-id +11 more]' for a group. At most `limit` lines, then one line that says how many
    groups and rows are left. `word` replaces the verdict (ASK for rows that carry a question). A text longer than
    LINE_TEXT characters is cut and ends in " ...": the report holds it whole."""
    out = []
    shown = groups if limit is None else groups[:limit]
    for g in shown:
        f = g['rows'][0]
        text = str(f.get('text') or '')
        if len(text) > LINE_TEXT:
            text = text[:LINE_TEXT - 4].rstrip() + ' ...'
        text += _world_text(f)
        if g['count'] == 1:
            out.append('  %-7s %s  %s  %s  [%s]' % (word or g['verdict'], f.get('part'), g['rule'], text, f.get('id')))
        else:
            out.append('  %-7s %s  %s  x%d  %s  [%s +%d more]' % (word or g['verdict'], f.get('part'), g['rule'], g['count'], text,
                                                                  f.get('id'), g['count'] - 1))
    if len(shown) < len(groups):
        rest = groups[len(shown):]
        out.append('  ... and %d more group(s), %d row(s)' % (len(rest), sum(g['count'] for g in rest)))
    return out


# ---- the report of a run ---------------------------------------------------------------------------------------------------

def build(root, reg, resolved, run, command, stage, verdicts, plan=None, audit=False, intent=None, unreachable=None):
    """Assemble the report dict (see the top) from the verdicts of the parts.

    verdicts     list of gate_plan.part_verdict() results (project stage: one pseudo-part per .3mf project)
    plan         the plan of the run, for the unit counts; None for runs without units
    With audit every open finding of an inherited part (verdict key "inherited_part": the audit's baseline holds its
    present geometry) gets the state "inherited", and such a part never makes the exit code 1. A part that changed
    after the audit, or that has a stamp, is reported as in an ordinary run: an audit cannot make new damage
    "inherited". The same holds for a part an audit judges after the owner adopted his list (verdict key
    "proposed_part"): its rows stay open, are marked "proposed" and go into the proposal for `gate.py owner adopt`. A row that could not decide (NOT_RUN) stays "open" also then: it is no finding of the geometry. A finding that carries a question is never triaged: it
    is answered. On an inherited part it has the state "waiting" (verdict key "deferred_asked"): asked at the export.
    Every finding row also gets "cause" and "group" (see "Groups" at the top). Returns the report; write() stores it."""
    import gate_plan
    units = {}
    for u in (plan or {}).get('units') or []:
        if u.get('part'):
            c = units.setdefault(u['part'], {'done': 0, 'total': 0})
            c['total'] += 1
            c['done'] += 1 if u.get('state') in ('done', 'error', 'skipped') else 0
    parts, findings, questions, waivers, groups = [], [], [], [], []
    totals = {'block_open': 0, 'attest_open': 0, 'waived': 0, 'inherited': 0, 'not_run': 0, 'info': 0,
              'by_verdict': {'FAIL': 0, 'BLOCKED': 0, 'GROW': 0, 'NOT_RUN': 0}, 'by_facts': 0, 'waiting': 0}
    for v in verdicts:
        rows = []
        for state in ('open', 'asked', 'answered', 'waived', 'inherited', 'info', 'waiting'):
            for f in v.get('deferred_asked' if state == 'waiting' else state) or []:
                row = dict(f)
                row['id'] = f.get('id') or gate_features.finding_id(f)
                row['state'] = 'inherited' if (audit and state == 'open' and f.get('verdict') != 'NOT_RUN' and v.get('inherited_part', True)) else state
                if audit and state == 'open' and f.get('verdict') != 'NOT_RUN' and v.get('proposed_part'):
                    row['proposed'] = True            # judged by an audit after the owner adopted his list: open, and offered to him
                row['waiver'] = f.get('waiver')
                row['limit_text'] = _limit_text(reg, resolved, f)
                row['of'] = v.get('part')         # the row of "parts" it is listed under (a project finding names its own part)
                row['cause'] = cause_of(f.get('text'))
                row['group'] = group_id(row.get('rule') or '', row.get('verdict') or '', row['cause'])
                rows.append(row)
        findings += rows
        for state in ('open', 'asked', 'inherited', 'waived', 'answered', 'waiting'):
            for g in group_rows([r for r in rows if r['state'] == state]):
                groups.append({'id': g['id'], 'part': v.get('part'), 'rule': g['rule'], 'verdict': g['verdict'], 'state': state, 'cause': g['cause'],
                               'by': g['by'], 'count': g['count'], 'findings': [r['id'] for r in g['rows']]})
        count = lambda state: sum(1 for r in rows if r['state'] == state)      # noqa: E731
        open_q = [q for q in v.get('questions') or [] if not q.get('answer')]
        waiting = list(v.get('deferred_questions') or [])
        verdict = v.get('verdict')
        if audit and v.get('inherited_part') and verdict == 'fail':
            # the rows that made it "fail" are the inherited rows of this audit: what is left open could not decide
            verdict = 'incomplete' if (count('open') or v.get('not_run')) else 'attest' if (count('asked') or open_q) else 'inherited'
        by_facts = [q for q in v.get('all_questions') or [] if isinstance(q.get('answer'), dict) and q['answer'].get('kind') == 'facts']
        parts.append({'part': v.get('part'), 'sig12': gate_common.sig12(v['sig']) if v.get('sig') else None, 'state': v.get('state'),
                      'verdict': verdict, 'units': units.get(v.get('part'), {'done': 0, 'total': 0}), 'open': count('open'),
                      'asked': count('asked') + len(open_q), 'waived': count('waived'), 'not_run': list(v.get('not_run') or []),
                      'inherited': count('inherited'), 'answered': count('answered'), 'attested': v.get('attested', 0),
                      'stale': list(v.get('stale') or []), 'by_facts': len(by_facts), 'waiting': len(waiting),
                      'inherited_part': bool(v.get('inherited_part')), 'proposed_part': bool(v.get('proposed_part'))})
        totals['block_open'] += count('open')
        totals['attest_open'] += count('asked') + len(open_q)
        totals['waived'] += count('waived')
        totals['inherited'] += count('inherited')
        totals['info'] += count('info')
        totals['by_facts'] += len(by_facts)
        totals['waiting'] += len(waiting)
        for r in rows:
            if r['state'] == 'open' and r.get('verdict') in totals['by_verdict']:
                totals['by_verdict'][r['verdict']] += 1
        totals['not_run'] += len(v.get('not_run') or []) + sum(1 for r in rows if r['verdict'] == 'NOT_RUN' and r['state'] in ('open', 'inherited'))
        mark = {q.get('q') for q in waiting}
        questions += [dict(q, waiting=True) if q.get('q') in mark else q for q in (v.get('all_questions') or v.get('questions') or [])]
        waivers += [dict({k: w.get(k) for k in ('waiver', 'rule', 'part', 'state')}, **({'errors': w['errors']} if w.get('errors') else {}))
                    for w in v.get('waivers') or []]
    names = set()
    for r in gate_registry.rules(reg, stage=stage, deferred=False):
        if isinstance(r.get('check'), dict) and r['check'].get('judge'):
            names.update(r.get('params') or [])
    read = sorted(gate_params.values(resolved, sorted(names))) if names else []
    params = [{'name': n, 'value': resolved[n]['value'], 'tag': resolved[n]['tag'], 'from': resolved[n]['from']} for n in read if n in resolved]
    return {'run': run, 'command': command, 'stage': stage, 'time': gate_common.now(), 'mode': gate_common.load_project(root)['mode'],
            'audit': bool(audit), 'intent': list(intent) if intent is not None else None, 'parts': parts, 'findings': findings,
            'questions': questions, 'waivers': waivers, 'deferred': sorted(r['id'] for r in gate_registry.rules(reg, deferred=True)),
            'unreachable': list(unreachable or []), 'params': params, 'totals': totals, 'groups': groups,
            'over_budget': gate_plan.over_budget(plan), 'exit': gate_plan.exit_code(verdicts, audit=audit)}


def summary_line(report):
    """One line with the counts by verdict:
    "gate run <run>: 3 parts, 3 open (2 FAIL, 1 GROW), 4 questions (2 answered by facts), 1 not run -> exit 1".
    The open rows are split by verdict whenever there are any; "not run" counts the checks with no result (rows with
    the verdict NOT_RUN and rules with nothing stored); the tail names what else there is (inherited, waived,
    unreachable, standing questions that wait at inherited parts)."""
    t = report.get('totals') or {}
    by = t.get('by_verdict') or {}
    split = ', '.join('%d %s' % (by[k], k) for k in VERDICT_ORDER if by.get(k))
    unit = 'projects' if report.get('stage') == 'project' else 'parts'
    bits = ['%d %s' % (len(report.get('parts') or []), unit), '%d open%s' % (t.get('block_open', 0), (' (%s)' % split) if split else ''),
            '%d questions%s' % (t.get('attest_open', 0), (' (%d answered by facts)' % t['by_facts']) if t.get('by_facts') else ''),
            '%d not run' % t.get('not_run', 0)]
    if t.get('inherited'):
        bits.append('%d inherited' % t['inherited'])
    if t.get('waived'):
        bits.append('%d waived' % t['waived'])
    if t.get('waiting'):
        bits.append('%d questions wait (inherited parts)' % t['waiting'])
    gone = sum(1 for p in report.get('parts') or [] if p.get('verdict') == 'unreachable')
    if gone:
        bits.append('%d unreachable' % gone)
    tail = {EXIT_MORE: ' (more calls needed)', EXIT_INFRA: ' (nothing could be checked)'}.get(report.get('exit'), '')
    if report.get('audit'):
        tail += ' (audit: nothing blocks)' if report.get('exit') in (EXIT_PASS, EXIT_MORE) else ' (audit: what changed after the audit blocks as usual)'
    return 'gate %s %s: %s -> exit %s%s' % (report.get('command'), report.get('run'), ', '.join(bits), report.get('exit'), tail)


def _finding_md(f, fix=True):
    head = '- **%s %s %s**' % (f.get('id'), f.get('rule'), f.get('verdict'))
    if f.get('measure') is not None:
        head += ': %s' % _measure(f)
        if f.get('limit') is not None:
            head += ' against %s' % (f.get('limit_text') or f.get('limit'))
    lines = [head, '  - %s' % f.get('text'), '  - where: %s%s' % (_where(f.get('anchor')), ' (part frame)%s' % _world_text(f) if _world_text(f) else '')]
    if f.get('of') and f.get('of') != f.get('part'):
        lines.append('  - part: %s' % f.get('part'))
    if f.get('by'):
        lines.append('  - stopped by: %s' % ', '.join(f['by']))
    if fix and f.get('fix'):
        lines.append('  - first: %s' % f['fix'][0])
        lines += ['  - then: %s' % step for step in f['fix'][1:]]
    if f.get('waiver') is not None:
        lines.append('  - waiver: %s' % f['waiver'])
    if f.get('triage'):
        lines.append('  - triage item %s, decision: %s' % (f['triage'], f.get('decision') or 'none yet'))
    if isinstance(f.get('answer'), dict):
        lines.append('  - answered (%s): %s' % (f['answer'].get('kind'), f['answer'].get('value')))
    return lines


def _group_md(g):
    """A group of equal rows as one block: the count, the measured range against the limit, the text of one row, what
    stops the fix, and the places with their ids."""
    f = g['rows'][0]
    head = '- **%s %s x%d** (%s)' % (g['rule'], g['verdict'], g['count'], g['id'])
    rng = _measure_range(g['rows'])
    if rng:
        head += ': %s' % rng
        if f.get('limit') is not None:
            head += ' against %s' % (f.get('limit_text') or f.get('limit'))
    lines = [head, '  - %s' % f.get('text')]
    if g['by']:
        lines.append('  - stopped by: %s' % ', '.join(g['by']))
    lines.append('  - where: %s' % _places(g['rows']))
    if len(g['rows']) > PLACES_SHOWN:
        lines.append('  - every id of this group is in the JSON report ("groups", id %s)' % g['id'])
    waivers = sorted({str(r['waiver']) for r in g['rows'] if r.get('waiver') is not None})
    if waivers:
        lines.append('  - waiver: %s' % ', '.join(waivers))
    if any(r.get('triage') for r in g['rows']):
        lines.append('  - triage items %s: %s' % (_ids([r.get('triage') for r in g['rows'] if r.get('triage')]),
                                                  _decisions([dict(r, decision=r.get('decision')) for r in g['rows']]).replace('OPEN', 'none yet')))
    answers = sorted({'%s: %s' % (r['answer'].get('kind'), r['answer'].get('value')) for r in g['rows'] if isinstance(r.get('answer'), dict)})
    if answers:
        lines.append('  - answered (%s)' % '; '.join(answers))
    return lines


def _rows_md(rows):
    """The rows of one state of one part: groups, blockers first. The fix order of a rule is printed once, after the
    last group of that rule (under that group when the rule has one group, else as a line of its own)."""
    out, groups = [], group_rows(rows)
    for k, g in enumerate(groups):
        out += _finding_md(g['rows'][0], fix=False) if g['count'] == 1 else _group_md(g)
        if g['verdict'] == 'NOT_RUN' or any(x['rule'] == g['rule'] and x['verdict'] != 'NOT_RUN' for x in groups[k + 1:]):
            continue
        same = [x for x in groups if x['rule'] == g['rule'] and x['verdict'] != 'NOT_RUN']
        fix = next((r.get('fix') for x in same for r in x['rows'] if r.get('fix')), None)
        if not fix:
            continue
        if len(same) > 1:
            out.append('- fix order of %s, for its %d rows above:' % (g['rule'], sum(x['count'] for x in same)))
        out.append('  - first: %s' % fix[0])
        out += ['  - then: %s' % step for step in fix[1:]]
    return out


def render_md(report):
    """The report as markdown: one summary line, then per part its verdict and its rows as groups (equal rows are one
    block with the count and the places; the fix order of a rule is printed once per part), blockers first: what failed,
    what could not decide and what makes it run, what is asked, what is inherited. Then the open questions, the
    answers the gate gave itself from the facts, the answers a pass rests on, the waiver states and the deferred rules.
    Plain text, no project knowledge. Every single finding with its id is in the JSON report."""
    out = ['# Gate %s %s (%s stage)' % (report.get('command'), report.get('run'), report.get('stage')), '', summary_line(report), '',
           'Time %s. Mode %s.%s%s' % (report.get('time'), report.get('mode'), ' Audit: findings are recorded as inherited, nothing blocks.' if report.get('audit') else '',
                                    (' Intent: %s.' % ', '.join(report['intent'])) if report.get('intent') else ''), '']
    parts = report.get('parts') or []
    if parts:
        out += ['| part | state | verdict | units | open | questions | waived | inherited |', '|---|---|---|---|---|---|---|---|']
        out += ['| %s | %s | %s | %d/%d | %d | %d | %d | %d |' % (p.get('part'), p.get('state') or '-', p.get('verdict'), (p.get('units') or {}).get('done', 0),
                                                              (p.get('units') or {}).get('total', 0), p.get('open', 0), p.get('asked', 0),
                                                              p.get('waived', 0), p.get('inherited', 0)) for p in parts]
        out.append('')
    findings, questions = report.get('findings') or [], report.get('questions') or []
    for p in parts:
        mine = [f for f in findings if f.get('of', f.get('part')) == p.get('part')]
        asks = [q for q in questions if q.get('part') == p.get('part')]
        verdict = p.get('verdict')
        if verdict == 'pass_attested':
            verdict = 'pass_attested(%d)' % p.get('attested', 0)
        out += ['## %s: %s' % (p.get('part'), verdict), '']
        if p.get('inherited_part'):
            out += ['An inherited part: the audit judged this geometry and it has not changed since. It has no stamp. Its rows are on the '
                    'triage list; its standing questions are asked when it changes, and at its export.', '']
        if p.get('not_run'):
            out += ['Nothing stored for: %s. A check that did not run has not passed.' % ', '.join(p['not_run']), '']
        if p.get('stale'):
            out += ['Judged by an older version of the judge: %s. `gate.py run --offline` judges the stored facts again.' % ', '.join(p['stale']), '']
        failed = [f for f in mine if f.get('state') == 'open' and f.get('verdict') != 'NOT_RUN']
        undecided = [f for f in mine if f.get('state') == 'open' and f.get('verdict') == 'NOT_RUN']
        for title, rows in (('Open: these stop the stamp', failed),
                            ('Could not decide: these count as open, each row says what makes the check run', undecided),
                            ('Asked: closed by an answer or a waiver', [f for f in mine if f.get('state') == 'asked']),
                            ('Questions about a feature that wait: asked when the part changes, and at its export', [f for f in mine if f.get('state') == 'waiting']),
                            ('Inherited: items of the triage list', [f for f in mine if f.get('state') == 'inherited']),
                            ('Waived by the owner', [f for f in mine if f.get('state') == 'waived']),
                            ('Closed by an answer', [f for f in mine if f.get('state') == 'answered'])):
            if rows:
                out += ['### %s' % title, '']
                out += _rows_md(rows)
                out.append('')
        open_q = [q for q in asks if not q.get('answer') and not q.get('waiting')]
        if open_q:
            out += ['### Questions open', '']
            for q in open_q:
                out.append('- **%s %s**: %s (evidence: %s)' % (q.get('q'), q.get('rule'), q.get('question'), ', '.join(q.get('evidence') or [])))
                out += ['  - "%s"' % text for text in q.get('labels') or []]
                out += ['  - the facts: %s' % text for text in q.get('facts') or []]
            out.append('')
        waiting = [q for q in asks if not q.get('answer') and q.get('waiting') and not q.get('finding')]
        if waiting:
            out += ['### Standing questions that wait: %d' % len(waiting), '',
                    'Not asked now, because nothing about this part changed. Asked when it changes, and at its export: %s.'
                    % ', '.join(sorted(q.get('rule') or '' for q in waiting)), '']
        by_facts = [q for q in asks if isinstance(q.get('answer'), dict) and q['answer'].get('kind') == 'facts']
        if by_facts:
            out += ['### Answered by facts', '', 'The gate gave these answers itself. They hold while the facts of the part say so, and they are no statement of the agent.', '']
            out += ['- %s %s: %s. %s' % (q.get('q'), q.get('rule'), q['answer'].get('value'), q['answer'].get('proof')) for q in by_facts]
            out.append('')
        done_q = [q for q in asks if q.get('answer') and q not in by_facts]
        if done_q:
            out += ['### Answers this verdict rests on', '']
            out += ['- %s %s: %s = %s' % (q.get('q'), q.get('rule'), q['answer'].get('kind'), q['answer'].get('value')) for q in done_q]
            out.append('')
        info = [f for f in mine if f.get('state') == 'info']
        if info:
            out += ['%d informational rows (they never block).' % len(info), '']
    if report.get('waivers'):
        out += ['## Waivers', '']
        out += ['- %s %s on %s: %s%s' % (w.get('waiver'), w.get('rule'), w.get('part'), w.get('state'),
                                        {'expired': ' (the feature changed since: the finding is open again)', 'orphan': ' (no feature at that place any more)',
                                         'invalid': ' (%s)' % '; '.join(w.get('errors') or [])}.get(w.get('state'), '')) for w in report['waivers']]
        out.append('')
    if report.get('unreachable'):
        out += ['## Not reached', ''] + ['- %s: its parts keep their stamps, nothing new was checked' % u for u in report['unreachable']] + ['']
    if report.get('over_budget'):
        import gate_plan
        out += ['## Units over their budget', '', 'A unit must answer within its budget. Each line is a defect of a probe to report, not a finding of a part.', '']
        out += ['- %s' % line[len('OVER BUDGET  '):] for line in gate_plan.over_lines(report['over_budget'])] + ['']
    if report.get('deferred'):
        out += ['## Registered, not checked yet', '', ', '.join(report['deferred']), '']
    if report.get('params'):
        out += ['## Parameters the judges read', '', '| parameter | value | tag | from |', '|---|---|---|---|']
        out += ['| %s | %s | %s | %s |' % (p.get('name'), p.get('value'), p.get('tag'), p.get('from')) for p in report['params']]
        out.append('')
    return '\n'.join(out)


def write(root, report):
    """Write gate/reports/<run>.json, <run>.md and latest.json. Returns (json path, md path)."""
    folder = gate_path(root, 'reports')
    jpath = write_json(os.path.join(folder, '%s.json' % report['run']), report)
    mpath = os.path.join(folder, '%s.md' % report['run'])
    tmp = '%s.tmp%d' % (mpath, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(render_md(report))
    os.replace(tmp, mpath)
    write_json(os.path.join(folder, 'latest.json'), report)
    return jpath, mpath


def load_latest(root):
    """The newest report (gate/reports/latest.json), or None."""
    return read_json(gate_path(root, 'reports', 'latest.json'), None)


def cmd_report(root, args):
    """gate.py report [--last]. Prints the md of the latest report (args.last or no argument). Returns the report's
    "exit"; 4 when there is no report yet."""
    report = load_latest(root)
    if not isinstance(report, dict):
        raise GateError('no report yet: gate.py run writes the first one')
    print(render_md(report))
    return int(report.get('exit') or 0)


# ---- status and digest: files only -----------------------------------------------------------------------------------------

def _stl_hash(root, relpath, cache, deadline):
    """sha256 of an STL, from the cache while its size and time are unchanged. None when the time for hashing is used up."""
    path = os.path.join(root, *relpath.split('/'))
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = cache.get(relpath)
    if isinstance(hit, dict) and hit.get('size') == st.st_size and hit.get('mtime') == st.st_mtime:
        return hit.get('sha256')
    if deadline is not None and time.time() > deadline:
        return None
    sha = gate_common.sha256_file(path)
    cache[relpath] = {'size': st.st_size, 'mtime': st.st_mtime, 'sha256': sha}
    return sha


def _stl_states(root, layout, census, hash_budget_s, stamped=None):
    """{relative path: "valid" | "stale" | "none" | "unknown"} of every STL in a registered STL folder, by the cheap test:
    a stamp exists for exactly these bytes, and it is the stamp of the part's current signature. (`gate.py verify` is
    the full test.) stamped: the part ids whose stamp is valid today (status computes them with gate_stamp.verify); an
    STL whose stamp matched by its bytes but whose part is not among them is "stale" too (a judge, a parameter or the
    facts moved since)."""
    folders = []
    for entry in list(layout.get('documents') or []) + list(layout.get('projects') or []):
        d = (entry.get('stl_dir') or '').strip('/')
        if d and d not in folders:
            folders.append(d)
    stls = []
    for d in folders:
        full = os.path.join(root, *d.split('/'))
        if os.path.isdir(full):
            stls += ['%s/%s' % (d, n) for n in sorted(os.listdir(full)) if n.lower().endswith('.stl')]
    if not stls:
        return {}
    by_sha = {}
    base = gate_path(root, 'stamps')
    for dirpath, _, names in os.walk(base):
        for n in names:
            if n.endswith('.json'):
                try:
                    s = read_json(os.path.join(dirpath, n), {})
                except GateError:
                    continue
                if isinstance(s, dict) and s.get('stl_sha256'):
                    by_sha.setdefault(s['stl_sha256'], []).append(s)
    cache_path = gate_path(root, 'run', 'stl_hashes.json')
    try:
        cache = read_json(cache_path, {})
    except GateError:
        cache = {}
    before = json.dumps(cache, sort_keys=True)
    deadline = (time.time() + hash_budget_s) if hash_budget_s is not None else None
    out = {}
    for rel in stls:
        sha = _stl_hash(root, rel, cache, deadline)
        if sha is None:
            out[rel] = 'unknown'
            continue
        stamps = by_sha.get(sha) or []
        head = [s for s in stamps if (census['parts'].get(s.get('part')) or {}).get('sig') == s.get('sig')]
        if head and stamped is not None and not any(s.get('part') in stamped for s in head):
            head = []                                    # the bytes are stamped, the stamp no longer holds
        out[rel] = 'valid' if head else 'stale' if stamps else 'none'
    if json.dumps(cache, sort_keys=True) != before:
        try:
            write_json(cache_path, cache)
        except OSError:
            pass
    return out


def _slice_owed(root, layout):
    """[(project key, "never" | "older")] of the registered .3mf projects whose slice record (gate/run/slice/<key>.json)
    is missing, or older than the file: print.overhang_angle and print.slice_support_growth stay NOT_RUN at `gate.py
    verify` until `gate.py slice KEY`. Only modification times are read."""
    from probes import project_slice
    out = []
    for e in layout.get('projects') or []:
        if not isinstance(e, dict) or not e.get('file') or not e.get('key'):
            continue
        path = e['file'] if os.path.isabs(e['file']) else os.path.join(root, *e['file'].split('/'))
        try:
            mine = os.path.getmtime(path)
        except OSError:
            continue
        rec = project_slice.record_path(root, e['key'])
        if not os.path.isfile(rec):
            out.append((e['key'], 'never'))
        elif os.path.getmtime(rec) < mine:
            out.append((e['key'], 'older'))
    return out


def first_export_parts(root, parts, census=None):
    """The parts of `parts` that were never exported through the gate and have an STL in the folder (an older
    exporter's): before their first export the bed face is taken from that file as it is. An export that writes other
    geometry asks print.bed_face once (gate_plan.print_frame: "earlier geometry"), and the next export uses the STL it
    wrote (gate_stamp._kept_export)."""
    import gate_plan, gate_stamp
    census = census if census is not None else gate_common.load_census(root)
    stamped = {s.get('part') for s in gate_stamp.all_stamps(root)}
    out = []
    for pid in parts:
        e = census['parts'].get(pid) or {}
        if pid in stamped or not e.get('stl') or e.get('source') == 'generated' or gate_plan._exported_geometry(root, pid, e.get('sig')):
            continue
        if os.path.isfile(os.path.join(root, *str(e['stl']).replace(os.sep, '/').split('/'))):
            out.append(pid)
    return out


def first_export_line(parts, rows=None):
    return ('  first export of %d part(s) never exported through the gate (%s): the bed face is taken from the STL in the folder. An export '
            'that writes other geometry (an older exporter, a model changed since) stops once at print.bed_face: answer it, then export '
            'again (the STL it wrote is used, not exported twice)' % (len(parts), ', '.join(parts) if rows is None else _some(parts)))


def status(root, reg=None, hash_budget_s=None):
    """The state of the project from files only: {"mode", "parts": n, "changed": [part ids with no stamp for their
    current signature], "open": [findings of the latest judgement that block], "questions": [open questions],
    "unstamped_stls": [paths in STL folders with no valid stamp], "triage_open": n, "last_run": run id or None,
    "last_scan": time or None}. Runs nothing in Fusion, judges nothing.

    "declared_outside": gate_guard.outside_summary() (the objects the owner declared outside the gate, per project).
    "measure_again": {"units", "work_s", "unknown"}: the units stale_units() names and their Fusion work by their last
    measurement ("unknown": units with no cost stored). Also: "unchecked" (parts changed since they were last CHECKED: their present geometry was never judged, or a unit
    of theirs must run again because its own record no longer holds, gate_plan.stale_units: also a stamped part whose
    neighbour moved), "unchecked_why" ({part: "<probe>: why"} of the latter), "checked_ids" (checked at the present
    model, no valid stamp), "stamped_ids" (a valid stamp and nothing to measure again), "inherited" (n findings that are triage
    items), "stale" (parts judged by an older judge), "stl_states" ({path: valid | stale | none | unknown}),
    "unfinished" ({"run", "done", "total"} of a run that needs more calls, or None), "unreachable" (document keys the
    last scan found closed), "registered" (documents and generators of gate/layout.json), "waiting" (standing questions
    of inherited parts that are not asked yet: they are no open questions), "inherited_parts" (how many parts are
    inherited: judged by the audit, not changed since, no stamp), "inherited_ids" (which), "over_budget" (the rows of
    gate_plan.over_budget() for the last scan and the current run: calls that worked longer than their unit's budget,
    calls that got no answer and left no result, units given up) and
    "waiting_call" (a line about the unit that was sent and has not answered yet, or None) and "stale_references"
    (gate_plan.stale_references of every document: linked documents used in an older version than their newest). "changed" still lists
    them (they have no stamp); the text of the status counts and names them apart. hash_budget_s limits the time spent hashing STLs that
    are not in the cache yet; the ones left over are "unknown" and are not counted as unstamped."""
    import gate_plan
    reg = reg or gate_registry.load_default(root)
    project, layout, census = gate_common.load_project(root), gate_common.load_layout(root), gate_common.load_census(root)
    live = [p for p in sorted(census['parts']) if not census['parts'][p].get('gone')]
    changed = gate_plan.changed_parts(root, census)
    export_rules = {r['id'] for r in gate_registry.rules(reg, stage='export')}
    open_, questions, unchecked, stale, inherited, waiting, inherited_parts = [], [], [], [], 0, 0, []
    for pid in live:
        sig = census['parts'][pid].get('sig')
        judged = (read_json(gate_common.facts_path(root, pid, sig), {}) or {}).get('judged') if sig else None
        if not judged:
            if pid in changed:
                unchecked.append(pid)
            continue
        stages = ('design', 'export') if export_rules & set(judged) else ('design',)
        v = gate_plan.part_verdict(root, reg, pid, stages=stages, defer_inherited=True)
        open_ += v['open']
        questions += v['questions'] + v['ask_questions']
        inherited += len(v['inherited'])
        waiting += len(v.get('deferred_questions') or [])
        if v.get('inherited_part'):
            inherited_parts.append(pid)
        if v['stale']:
            stale.append(pid)
        if (v['not_run'] or v['verdict'] == 'unreachable') and v['verdict'] in ('incomplete', 'unreachable') and pid in changed:
            unchecked.append(pid)                # a rule with nothing stored. A row that could not decide (NOT_RUN) is listed as an open row instead
    # changed since it was last CHECKED: a unit whose stored facts no longer hold (its geometry, its place, a neighbour it
    # read, a parameter or the probe changed). Stamped or not: a moved neighbour can close a hole of a stamped part
    try:
        stale_units = gate_plan.stale_units(root, reg, census=census, layout=layout, project=project)
    except GateError:
        stale_units = {}
    why, again = {}, {'units': 0, 'work_s': 0.0, 'unknown': 0}
    for pid, units in sorted(stale_units.items()):
        first = next(('%s: %s' % (probe, w) for probe, w in sorted(units.items()) if w), None)
        if first:
            why[pid] = first
            if pid not in unchecked:
                unchecked.append(pid)
            stored = gate_plan._stored_units(root, pid, census['parts'].get(pid) or {})
            for probe, w in units.items():                  # what measuring them again costs, from their last measurement
                if w:
                    cost = gate_plan._expected_cost(stored.get(probe))
                    again['units'] += 1
                    if cost is None:
                        again['unknown'] += 1
                    else:
                        again['work_s'] += cost
    unchecked.sort()
    stamped = [p for p in live if p not in changed and p not in unchecked]
    checked = [p for p in live if p in changed and p not in unchecked]
    states = _stl_states(root, layout, census, hash_budget_s, stamped=set(live) - set(changed))     # a valid stamp today (verify)
    plan = gate_plan.load_plan(root)
    unfinished = None
    if plan and plan.get('command') == 'run':
        n, m = gate_plan.progress(plan)
        if n < m:
            unfinished = {'run': plan['run'], 'done': n, 'total': m}
    over = gate_plan.scan_over_budget(root) + (gate_plan.over_budget(plan) if plan and plan.get('command') == 'run' else [])
    latest = load_latest(root) or {}
    import gate_guard
    return {'declared_outside': gate_guard.outside_summary(root, layout), 'slice_owed': _slice_owed(root, layout), 'mode': project['mode'], 'parts': len(live), 'changed': changed, 'open': open_, 'questions': questions,
            'unstamped_stls': sorted(p for p, s in states.items() if s in ('none', 'stale')), 'triage_open': sum(1 for i in triage_of(root) if not i.get('decision')),
            'last_run': latest.get('run'), 'last_scan': census.get('scanned'), 'unchecked': unchecked, 'inherited': inherited, 'stale': stale,
            'stl_states': states, 'unfinished': unfinished, 'unreachable': sorted(k for k, d in census['docs'].items() if d.get('open') is False),
            'registered': len(layout.get('documents') or []) + len(layout.get('generators') or []), 'waiting': waiting,
            'inherited_parts': len(inherited_parts), 'inherited_ids': inherited_parts, 'over_budget': over,
            'waiting_call': gate_plan.waiting_line(root), 'unchecked_why': why, 'checked_ids': checked, 'stamped_ids': stamped,
            'measure_again': dict(again, work_s=round(again['work_s'], 1)), 'first_export': first_export_parts(root, checked, census),
            'stale_references': [line for k in sorted(census['docs']) for line in gate_plan.stale_references(census, k)]}


def _some(names, n=4):
    names = list(names)
    return ', '.join(names[:n]) + (' (+%d more)' % (len(names) - n) if len(names) > n else '')


def _minutes(seconds):
    return '%d s' % round(seconds) if seconds < 90 else '%d min' % round(seconds / 60.0)


def _status_lines(st, rows=None):
    """The status as text. rows None = everything; a number = at most that many open rows (the digest)."""
    settled = set(st.get('inherited_ids') or []) - set(st['unchecked'])       # judged by the audit, not changed since: not "changed"
    unchecked = set(st['unchecked'])
    checked = st['checked_ids'] if 'checked_ids' in st else [p for p in st['changed'] if p not in unchecked]
    stamped = len(st['stamped_ids']) if 'stamped_ids' in st else max(0, st['parts'] - len(set(st['changed']) | unchecked))
    head = ['gate [%s]: %d parts' % (st['mode'], st['parts']), '%d changed, not checked' % len(unchecked),
            '%d checked, no stamp' % len(checked), '%d stamped' % stamped,
            '%d open findings' % len(st['open']), '%d questions' % len(st['questions']),
            '%d STLs without a valid stamp' % len(st['unstamped_stls'])]
    if st['triage_open'] or st['inherited']:
        head.append('triage: %d not decided' % st['triage_open'])
    out = [' | '.join(head)]
    for row in st.get('declared_outside') or []:    # let through without a stamp on the owner's word: one line per project, never left out
        out.append('  ' + row['line'])
    if st['last_scan'] is None:
        out.append('  nothing scanned yet: `gate.py run --changed` scans the open documents first')
    if st['unfinished']:
        out.append('  run %s is not finished: %d/%d units (`gate.py run` goes on with it)' % (st['unfinished']['run'], st['unfinished']['done'], st['unfinished']['total']))
    if st.get('waiting_call'):
        out.append('  ' + st['waiting_call'])
    for line in st.get('stale_references') or []:   # measured against an old version of a linked document: said before any row
        out.append('  OUT OF DATE ' + line)
    if st.get('over_budget'):                       # a defect of a probe, said until the next run: one line each (the digest: the first two)
        import gate_plan
        lines = gate_plan.over_lines(st['over_budget'])
        shown = lines if rows is None else lines[:2]
        out += ['  ' + line for line in shown]
        if len(shown) < len(lines):
            out.append('  ... and %d more units over their budget (`gate.py status`)' % (len(lines) - len(shown)))
    groups = group_rows(st['open'])                 # equal rows of a part are one line; the report holds every row
    limit = RUN_LINES if rows is None else rows
    lines = group_lines(groups, limit)
    if len(groups) > limit:
        lines[-1] += ' (`gate.py status --json` lists every row)'
    out += lines
    qs = st['questions'] if rows is None else st['questions'][:2]
    if rows is None and len(qs) > RUN_LINES:
        qs = []
    for q in qs:
        out.append('  ASK     %s  %s  %s  [%s]' % (q.get('part'), q.get('rule'), q.get('question'), q.get('q')))
    if len(qs) < len(st['questions']):
        out.append(('  ... and %d more questions (`gate.py attest list`)' if qs else '  %d questions (`gate.py attest list` shows them by rule)')
                   % (len(st['questions']) - len(qs)))
    if st.get('waiting'):
        out.append('  %d questions wait at %d inherited parts: asked when a part changes, and at its export' % (st['waiting'], st.get('inherited_parts', 0)))
    if st['last_scan'] is not None:                 # the counts are those of the last scan: an edit since is not in them
        out.append('  as of the scan at %s: what changed in Fusion after it is seen by the next `gate.py scan` or `run`' % st['last_scan'])
    again = st.get('measure_again') or {}
    if again.get('units'):
        out.append('  to measure again: %d unit(s), about %s of Fusion work by their last measurement%s (`gate.py run --changed`)'
                   % (again['units'], _minutes(again.get('work_s') or 0.0),
                      ', %d of them of unknown cost (up to the budget of a call each)' % again['unknown'] if again.get('unknown') else ''))
    if st['unchecked']:
        out.append('  changed and not checked: %s' % (', '.join(st['unchecked']) if rows is None else _some(st['unchecked'])))
        whys = st.get('unchecked_why') or {}
        for pid in (sorted(whys) if rows is None else sorted(whys)[:2]):
            out.append('    %s: %s' % (pid, whys[pid]))
        if rows is not None and len(whys) > 2:
            out.append('    ... and %d more (`gate.py status`)' % (len(whys) - 2))
    if settled:
        out.append('  %d inherited parts: judged by the audit, not changed since, no stamp (%s)' % (len(settled), _some(sorted(settled))))
    passed = [p for p in st['changed'] if p not in st['unchecked'] and p not in settled]
    if passed:
        out.append('  checked, no stamp yet (not exported): %s' % (', '.join(passed) if rows is None else _some(passed)))
    first = [p for p in st.get('first_export') or [] if p in passed]
    if first:
        out.append(first_export_line(first, rows))
    if st['unstamped_stls']:
        out.append('  STLs without a valid stamp: %s' % (', '.join(st['unstamped_stls']) if rows is None else _some(st['unstamped_stls'])))
    unknown = [p for p, s in st['stl_states'].items() if s == 'unknown']
    if unknown:
        out.append('  %d STLs not looked at yet (`gate.py status` reads them)' % len(unknown))
    if st['stale']:
        out.append('  judged by an older judge (`gate.py run --offline`): %s' % _some(st['stale']))
    if st['unreachable']:
        out.append('  documents not open at the last scan: %s' % ', '.join(st['unreachable']))
    for key, how in st.get('slice_owed') or []:          # the project stage: its supports are measured by a slice, not by this status
        out.append('  project %s %s: python3 gate/tools/gate.py slice %s, then python3 gate/tools/gate.py verify'
                   % (key, 'was never sliced by the gate' if how == 'never' else 'changed since its last slice', key))
    return out


def digest(root, st=None):
    """The text the SessionStart hook prints: at most 15 lines. Mode, counts of status() (or of `st`, a status() the
    caller has), the first open rows, and the three commands (gate.py status, gate.py run --changed, gate.py export
    PART). Never raises: any problem gives a one-line "gate: status unavailable (<why>)". It judges every part: in a
    large project that takes seconds, so the hook reads the copy refresh_digest() keeps (gate_digest.py)."""
    try:
        lines = _status_lines(st if isinstance(st, dict) else status(root, hash_budget_s=0.3), rows=4)
        if len(lines) > DIGEST_LINES - 1:
            lines = lines[:DIGEST_LINES - 2] + ['  ... (`gate.py status` shows the rest)']
        return '\n'.join(lines + [COMMANDS_LINE])
    except Exception as e:                       # the hook must never fail a session start
        return 'gate: status unavailable (%s: %s)' % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else '')


def _digest_by():
    """"<plugin> <version>" of the code that runs: the plugin's own version, or the one gate/LOCK.json beside a vendored
    copy names."""
    import gate_vendor
    try:
        own = gate_vendor.own_plugin_root()
        version = gate_vendor.plugin_version(own) if own else (read_json(os.path.join(gate_common.tools_dir(), '..', 'LOCK.json'), {}) or {}).get('version')
    except Exception:
        version = None
    return '%s %s' % (gate_vendor.PLUGIN_NAME, version or '?')


def refresh_digest(root, st=None, state=None):
    """Build the digest of `root` (from `st` when the caller has a status()) and keep it in gate/run/digest.json with
    the state of its inputs (gate_digest.py), for the SessionStart hook. `state`: gate_digest.inputs() taken BEFORE
    the status was computed, else taken here before the digest: a file that changes meanwhile makes the kept digest
    stale, never a stale digest look fresh. Never raises. Returns the text, or None."""
    try:
        import gate_digest
        tools, rules = gate_common.tools_dir(), gate_common.rules_dir()
        if state is None:
            state = gate_digest.inputs(root, tools, rules)
        text = digest(root, st)
        gate_digest.write(root, text, tools, rules, _digest_by(), state)
        return text
    except Exception:
        return None


def cmd_status(root, args):
    """gate.py status [--json] [--digest]. No Fusion. Prints status() as text, as JSON (args.json) or as the digest
    (args.digest). Returns 0 when nothing is open, 1 open block findings, 2 questions open.

    A changed part whose present geometry no run has judged counts as open: what was not checked has not passed.
    The same holds for a project with registered documents that was never scanned, and for an STL in a registered
    folder that has no valid stamp (`gate.py verify` fails on it as project.stl_without_stamp)."""
    import gate_digest
    try:
        state = gate_digest.inputs(root, gate_common.tools_dir(), gate_common.rules_dir())
    except Exception:
        state = None
    st = status(root)
    text = (refresh_digest(root, st, state) if state is not None else None) or digest(root, st)    # the hook prints this copy
    if args.digest:
        print(text)
    if args.json:
        print(json.dumps(st, indent=1, sort_keys=True))
    elif not args.digest:
        print('\n'.join(_status_lines(st)))
        print(COMMANDS_LINE)
    if st['open'] or st['unchecked'] or st['unstamped_stls'] or (st['last_scan'] is None and st['registered']):
        return EXIT_BLOCK
    return EXIT_ATTEST if st['questions'] else EXIT_PASS
