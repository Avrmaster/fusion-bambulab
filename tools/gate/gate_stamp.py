"""Stamps: the record that one geometry of one part passed the gate, and the export that earns it.

A stamp, gate/stamps/<doc>/<part>/<sig12>.json. One file per part per signature, never overwritten:

    {"part": "demo/motor_plate", "sig": "<B-rep signature, or the geom_hash of a generated STL>",
     "verdict": "pass" | "pass_attested", "attested": 0,
     "checks": {"inserts.wall": {"judge_hash": "...", "params": {"inserts.wall_target": 4.0, ...},
                                 "result": "pass" | "waived" | "attested" | "tolerated"}, ...},
     "stl": "stl/motor_plate.stl", "stl_sha256": "...", "geom_hash": "<meshid.geom_hash of the STL, model form>",
     "target": {"nozzle": 0.6, "line_width": 0.62, "material": "PETG"},
     "doc_version": 41, "time": "2031-02-03T10:31:02Z", "run": "<run id>"}

"checks" holds every block rule of the design and export stages that applies to the part (applicable_rules).
Optional keys: "facts" ({probe: 16 hex over the facts the judgement rests on}), "for" (the key of the .3mf project whose declared target the part was judged for), "answers" (the ids of
the questions whose answers the stamp rests on), "tolerated" (how many findings the owner's triage decision closed).

A stamp is VALID for a mesh when all hold (verify):
  - the mesh's geom_hash (model form) equals the stamp's;
  - the stamp's sig is the newest known signature of the part: the census (gate/census.json) names no other one, the
    part is not gone from its document, and no stamp of the part with another signature was written later;
  - when a target is asked for (the declared target of the .3mf project the mesh goes into): it is the stamp's target;
  - every block rule that applies to the part today is in "checks";
  - for each of them, the judge_hash equals today's hash of that judge (a changed judge re-opens only its own check;
    `gate.py run --offline` judges the stored facts again and export writes a new stamp);
  - for each of them, no parameter the check was judged with is STRICTER today than the value stored in the stamp (a
    tightened limit re-opens the check the same way; a loosened one does not: what passed 4.0 passes 3.0; a text or
    pattern parameter that changed at all re-opens it);
  - a check that rests on a waiver, an answer or a "tolerate" decision still has it: its stored findings at this
    signature are all closed today (a withdrawn waiver re-opens the check);
  - every answer the stamp names in "answers" is still stored, and an answer that is a waiver still has its waiver in
    gate/owner/waivers.json (a withdrawn waiver behind a question re-opens the stamp);
  - the facts of the stamped geometry are the ones the stamp rests on ("facts": {probe: digest}; a stamp without it:
    no probe entry was stored after the stamp), and no unit of them would be measured again because what it READ
    changed (a neighbour moved, came or changed, the part moved, a parameter it read changed: gate_deps.py). A moved
    neighbour that can close a hole of the part re-opens its stamp. Facts measured by a copy of the gate older than
    gate_deps.py hold no record of what they read: for them only the first test applies, and `gate.py run --changed`
    measures them again.
  - the print orientation and the tables each check was judged with (its "inputs": gate_plan.judge_inputs) are today's,
    or the checks whose inputs changed still pass when they are judged again from the stored facts (a part turned on its
    bed, or placed on a face after an export while it lay nowhere, is judged on that face; a stock list the owner
    changed in gate/owner/tables.json is held against the part).
Anything else is "stale" with the reason; no stamp at all is "none".
What does NOT kill a stamp: a new version of the plugin or of the vendored copy (the lock only says whether the copy is
intact; a probe text or a Fusion version that changed makes `run --changed` measure the units again, and the stamp goes
stale only when the facts then come out otherwise), a change to a judge of another rule, a change to a rule's text, a
new attest or advice rule.

A dead stamp is replaced, never edited: export writes the next stamp of the same signature beside the first one as
<sig12>.2.json, <sig12>.3.json, ... (write(..., supersede=True)). The same is how one geometry holds a stamp for two
targets, for example a production project and a test project. load() and head() give the newest of them.

A finding of the owner's triage list (gate/owner/triage.json) with the decision "tolerate", "rule_wrong" or "waive" does
not stop a stamp: the check gets the result "tolerated", the stamp is "pass_attested" and counts it in "tolerated", so
the report shows what the stamp rests on. Only an item with no decision or "fix" blocks the export.

Export (gate.py export PART): the part's design verdict must be pass or pass_attested and no inherited finding may block
it -> one short Fusion call reads the part's signature as it is NOW (the census is only as new as the last scan: a part
changed since then is refused, never reported as "already stamped") -> one fusion_export unit writes gate/run/quarantine/<doc>__<slug>.stl -> stl_mesh and stl_walls measure that file ->
the export-stage judges and export-stage questions -> only then the file moves into the document's STL folder and the
stamp is written. A part that fails stays in quarantine and the STL folder keeps the previous file; the next export
of the same signature for the same job judges that file again instead of exporting it from Fusion once more (an
export that stopped at a question costs one Fusion export, not two). The file in that
folder is the one the census names for the part ("stl": <stl_dir>/<slug>.stl, or the name "stl_names" of
gate/layout.json gave it at the last scan).

The stored design judgement of a part holds for ONE declared target (the one the last run or export judged it for). The
facts do not depend on the target, the judgement does (layer height, the tag of a fit). So export judges the design
stage again from the stored facts for the target it was asked for (--for KEY) before it reads the verdict: a part can
be exported for each .3mf project in turn."""
import os, re, time

import gate_common
from gate_common import GateError, BLOCKING, EXIT_PASS, EXIT_BLOCK, EXIT_ATTEST, EXIT_MORE, EXIT_INFRA

STAMP_KEYS = ('part', 'sig', 'verdict', 'attested', 'checks', 'stl', 'stl_sha256', 'geom_hash', 'target', 'doc_version', 'time', 'run')
VERDICTS = ('pass', 'pass_attested')
RESULTS = ('pass', 'waived', 'attested', 'tolerated')
_VOLATILE = ('time', 'run')                 # two stamps that differ only here say the same thing
_TARGET_KEYS = ('nozzle', 'line_width', 'material')
_MISSING = object()


# ---- small helpers ---------------------------------------------------------------------------------------------------

def _source(root, part, census=None, stamp=None):
    """"fusion" or "generated": where a part comes from. The census knows; a part it does not list is generated when
    gate/layout.json names it as a generator's part, or when its stamp's signature is the geometry hash of its STL."""
    census = census if census is not None else gate_common.load_census(root)
    entry = (census.get('parts') or {}).get(part)
    if entry and entry.get('source'):
        return entry['source']
    if stamp is not None and stamp.get('sig') and stamp.get('sig') == stamp.get('geom_hash'):
        return 'generated'
    try:
        if any(g.get('part') == part for g in gate_common.load_layout(root)['generators']):
            return 'generated'
    except GateError:
        pass
    return 'fusion'


def _target_key(target):
    """A target reduced to what a stamp is judged for, or None."""
    if not isinstance(target, dict):
        return None
    out = []
    for k in _TARGET_KEYS:
        v = target.get(k)
        out.append(round(float(v), 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
    return tuple(out)


def _target_text(target):
    if not isinstance(target, dict):
        return 'no target'
    return '%s mm nozzle, line %s, %s' % (target.get('nozzle'), target.get('line_width'), target.get('material'))


def _part_dir(root, part):
    return os.path.dirname(gate_common.stamp_path(root, part, 'x'))


def _stamp_files(root, part=None):
    """[(path, stamp)] of every readable stamp file (of one part, or of all). A file that is not a stamp is skipped:
    a broken stamp is no stamp."""
    base = _part_dir(root, part) if part else gate_common.gate_path(root, 'stamps')
    out = []
    for dirpath, _, names in os.walk(base):
        for n in sorted(names):
            if not n.endswith('.json'):
                continue
            path = os.path.join(dirpath, n)
            try:
                s = gate_common.read_json(path)
            except (GateError, OSError):
                continue
            if isinstance(s, dict) and isinstance(s.get('part'), str) and s.get('sig') and (part is None or s['part'] == part):
                out.append((path, s))
    out.sort(key=lambda ps: (str(ps[1].get('time') or ''), _file_no(ps[0]), ps[0]), reverse=True)
    return out


def _file_no(path):
    """1 for <sig12>.json, n for <sig12>.<n>.json: the later stamp of one signature wins a tie in time."""
    m = re.search(r'\.(\d+)\.json$', path)
    return int(m.group(1)) if m else 1


def _same(a, b):
    strip = lambda s: {k: v for k, v in s.items() if k not in _VOLATILE}
    return gate_common.canonical(strip(a)) == gate_common.canonical(strip(b))


def name_of(stamp):
    """"<part id>@<sig12>": how a stamp is named in other files (the ledger's write events, the guard's result)."""
    return '%s@%s' % (stamp['part'], gate_common.sig12(stamp['sig']))


# ---- the stamp -------------------------------------------------------------------------------------------------------

def applicable_rules(reg, entry, stages=('design', 'export')):
    """Sorted ids of the block rules of these stages that apply to a part (gate_registry.applies_to with the census
    entry's "source"): what a stamp's "checks" must hold."""
    import gate_registry
    source = (entry or {}).get('source') or 'fusion'
    return sorted(r['id'] for r in gate_registry.rules(reg, level='block')
                  if r['stage'] in stages and gate_registry.applies_to(r, source))


def _closed_by_answer(root, part, finding, cache):
    """Does an attest answer close this finding (gate_attest.closes)? False while gate_attest cannot say."""
    if not finding.get('ask'):
        return False
    try:
        import gate_attest
        if 'answers' not in cache:
            cache['answers'] = gate_attest.live_answers(root, part)       # an answer whose waiver is gone is no answer
        return bool(gate_attest.closes(finding, cache['answers']))
    except NotImplementedError:
        return False


def _closed_by_triage(root, part, finding, cache):
    """Is this finding an item of the owner's triage list with a decision that lets the part be exported ("tolerate",
    "rule_wrong", "waive": everything but no decision and "fix")? The item must match rule, part, anchor and context."""
    import gate_report
    if 'triage' not in cache:
        cache['triage'] = gate_report.triage_of(root, part)
    item = gate_report.triage_match(cache['triage'], dict(finding, part=finding.get('part') or part))
    return item is not None and item.get('decision') not in gate_report.BLOCKING_DECISIONS


def _still_closed(root, part, sig, rid):
    """Are the blocking findings stored for one rule at one signature all closed today (waiver, answer, triage
    decision)? None when nothing is stored for it (then nothing can be said)."""
    import gate_features
    j = (gate_common.load_facts(root, part, sig).get('judged') or {}).get(rid)
    if not isinstance(j, dict) or not isinstance(j.get('findings'), list):
        return None
    waivers = (gate_common.read_json(gate_common.gate_path(root, 'owner', 'waivers.json'), {}) or {}).get('waivers') or []
    blocking = [f for f in j['findings'] if f.get('verdict') in BLOCKING and f.get('part', part) == part]
    still, _w, _r = gate_features.apply_waivers(blocking, waivers, part=part)
    cache = {}
    return not [f for f in still if not _closed_by_triage(root, part, f, cache) and not _closed_by_answer(root, part, f, cache)]


_RESOLVED = {}              # (root, mtime of the owner's params, target) -> gate_params.resolve(): verify() runs once per stamp


def _resolved_for(root, reg, target):
    """Today's parameter values of this project for one target, read once per process and per change of the file."""
    import gate_params
    path = gate_common.gate_path(root, 'owner', 'params.json')
    key = (os.path.abspath(root), os.path.getmtime(path) if os.path.isfile(path) else None,
           gate_common.canonical(target) if isinstance(target, dict) else None, reg.get('sha256'))
    if key not in _RESOLVED:
        _RESOLVED.clear()
        _RESOLVED[key] = gate_params.resolve(reg, gate_params.load_overrides(root), target if isinstance(target, dict) else None)
    return _RESOLVED[key]


def _target_today(root, stamp, target=None):
    """The declared target a stamp's parameters are resolved for today: the one handed over (the project the mesh goes
    into); else what gate/project.json declares now for the project the stamp names in "for"; else, for a stamp that
    names none, the only declared target with the stamp's nozzle, line width and material, or among several the one
    that equals the stamp's target in every key; else the stamp's own target. The fit settings of a declared target may
    have changed since the stamp (the owner declared another hole compensation): a fit measured under the old ones
    resolves as D now, and its check is open again. A stamp is never judged against the target of ANOTHER project
    because that one happens to have the same nozzle and material."""
    if isinstance(target, dict):
        return target
    mine = stamp.get('target') if isinstance(stamp, dict) else None
    try:
        targets = gate_common.load_project(root).get('targets') or {}
    except GateError:
        return mine
    named = targets.get(stamp.get('for')) if isinstance(stamp, dict) and isinstance(stamp.get('for'), str) else None
    if isinstance(named, dict):
        return named
    same = [t for t in targets.values() if isinstance(t, dict) and _target_key(t) == _target_key(mine)]
    if len(same) == 1:
        return same[0]
    for t in same:
        if isinstance(mine, dict) and gate_common.canonical(t) == gate_common.canonical(mine):
            return t
    return mine


def _stricter_params(reg, resolved, rule, stored):
    """[(name, stored value, today's value)] of the parameters of one rule that are stricter today than when the check was
    judged, or that changed and have no order (a text, a pattern). A parameter the stamp does not hold counts: it was
    not judged with it."""
    import gate_params
    out = []
    for name, now in sorted(gate_params.values(resolved, rule.get('params') or []).items()):
        then = (stored or {}).get(name, _MISSING)
        if then is _MISSING:
            out.append((name, None, now))
            continue
        if then == now:
            continue
        d = reg['params'].get(name.split('[')[0]) or {}
        if gate_params.is_looser(d, now, then) and d.get('looser') in ('lower', 'higher'):
            continue                                    # looser today: what passed the stricter value still passes
        out.append((name, then, now))
    return out


def build(root, reg, resolved, part, sig, judged, stl_path, target, attested=0, doc_version=None, run=None, target_key=None, answers=None):
    """Make the stamp dict for a part that passed.

    judged    {rule id: {"judge_hash", "params", "findings"}} as stored in the facts file ("judged")
    stl_path  the promoted STL (its sha256 and meshid.geom_hash are read here)
    target_key  the key of the .3mf project `target` is declared for (stored as "for"), or None
    answers   the ids of the answered questions the pass rests on (stored as "answers": verify() holds each one
              against gate/attest and gate/owner/waivers.json), or None
    Each check's "result" is "pass" (no blocking finding), "waived" (every blocking finding has a valid waiver),
    "attested" (the rest were closed by answers) or "tolerated" (some were closed by the owner's triage decision
    "tolerate", "rule_wrong" or "waive"). GateError when a rule of applicable_rules() has no judgement or an
    open blocking finding: such a part gets no stamp. Also a GateError: a judgement made by an older judge, or with
    other parameter values than `resolved` gives today (judge again: `gate.py run --offline`)."""
    import gate_features, gate_judges, gate_params
    gate_common.split_part_id(part)
    if not sig:
        raise GateError('%s: no signature, no stamp' % part)
    if not os.path.isfile(stl_path):
        raise GateError('%s: the STL %s does not exist' % (part, stl_path))
    census = gate_common.load_census(root)
    entry = {'source': _source(root, part, census)}
    waivers = (gate_common.read_json(gate_common.gate_path(root, 'owner', 'waivers.json'), {}) or {}).get('waivers') or []
    judged = judged or {}
    checks, cache, by_answers, tolerated = {}, {}, 0, 0
    for rid in applicable_rules(reg, entry):
        rule = reg['rules'][rid]; name = rule['check']['judge']
        j = judged.get(rid)
        if not isinstance(j, dict) or 'findings' not in j:
            raise GateError('%s: rule %s has no judgement at signature %s: run the gate on this part first' % (part, rid, gate_common.sig12(sig)))
        if j.get('judge_hash') != gate_judges.judge_hash(name):
            raise GateError('%s: rule %s was judged by an older judge: judge again (gate.py run --offline)' % (part, rid))
        if resolved is not None:
            today = gate_params.values(resolved, rule.get('params') or [])
            stored = j.get('params') or {}
            moved = sorted(k for k, v in today.items() if stored.get(k, _MISSING) != v)
            if moved:
                raise GateError('%s: rule %s was judged with other values of %s: judge again (gate.py run --offline)' % (part, rid, ', '.join(moved)))
        blocking = [f for f in j.get('findings') or [] if f.get('verdict') in BLOCKING]
        still, _waived, _rows = gate_features.apply_waivers(blocking, waivers, part=part)
        left = [f for f in still if not _closed_by_triage(root, part, f, cache)]
        rest = [f for f in left if not _closed_by_answer(root, part, f, cache)]
        if rest:
            raise GateError('%s: %d open finding(s) of %s, no stamp: %s' % (part, len(rest), rid, rest[0].get('text') or rest[0].get('verdict')))
        by_answers += len(left)
        tolerated += len(still) - len(left)
        checks[rid] = {'judge_hash': j['judge_hash'], 'params': dict(j.get('params') or {}),
                       'result': 'pass' if not blocking else 'waived' if not still else 'tolerated' if len(left) < len(still) else 'attested'}
        inputs = {k: j[k] for k in ('print', 'tables') if isinstance(j.get(k), str)}
        if inputs and isinstance(j.get('tables_read'), list):
            inputs['tables_read'] = list(j['tables_read'])      # the tables its judge looked at: only these re-judge it
        if inputs:
            checks[rid]['inputs'] = inputs              # the print orientation and the tables it was judged with (verify)
    ids = gate_common.sibling('meshid').stl_ids(stl_path)
    n = max(int(attested or 0), by_answers)
    stamp = {'part': part, 'sig': sig, 'verdict': 'pass_attested' if (n or tolerated) else 'pass', 'attested': n, 'checks': checks,
             'stl': gate_common.rel(root, stl_path), 'stl_sha256': ids['sha256'], 'geom_hash': ids['geom_hash'],
             'target': dict(target) if isinstance(target, dict) else None, 'doc_version': doc_version,
             'time': gate_common.now(), 'run': run}
    if tolerated:
        stamp['tolerated'] = tolerated
    if isinstance(target_key, str) and target_key:
        stamp['for'] = target_key
    if answers:
        stamp['answers'] = sorted({str(q) for q in answers})
    measured = facts_digests(root, part, sig)
    if measured:
        stamp['facts'] = measured
    return stamp


def facts_digests(root, part, sig):
    """{probe: 16 hex over its stored facts} of the Fusion units of one geometry: what a stamp rests on (its "facts").
    verify() finds out from it that the part was measured again after the stamp and came out otherwise. The STL probes
    of the export stage are not in it: every export measures its own STL for its own target, and the stamp holds that
    file by its geom_hash."""
    probes = gate_common.load_facts(root, part, sig).get('probes') or {}
    return {k: gate_common.digest(e.get('facts')) for k, e in sorted(probes.items())
            if k.startswith('fusion_') and isinstance(e, dict) and not e.get('error') and isinstance(e.get('facts'), dict)}


def _measured_since(root, stamp):
    """Why the stored facts of the stamped geometry are no longer the ones the stamp rests on, or None. A stamp with
    "facts": a probe's facts differ from the digest it holds, or are gone. An older stamp (no "facts"): a probe entry
    was stored later than the stamp was written."""
    part, sig = stamp.get('part'), stamp.get('sig')
    probes = gate_common.load_facts(root, part, sig).get('probes') or {}
    held = stamp.get('facts')
    if isinstance(held, dict):
        now = facts_digests(root, part, sig)
        for k in sorted(held):
            if now.get(k) != held[k]:
                return '%s was measured again after the stamp and its facts %s' % (k, 'are gone or an error' if k not in now else 'differ')
        return None
    mine = str(stamp.get('time') or '')
    for k, e in sorted(probes.items()):
        if k.startswith('fusion_') and isinstance(e, dict) and mine and str(e.get('time') or '') > mine:
            return '%s was measured again after the stamp (%s)' % (k, e.get('time'))
    return None


def _neighbours_moved(root, reg, stamp, census):
    """Why a unit the stamp rests on would be measured again today because what it READ changed (gate_plan.stale_units:
    a neighbour moved or changed, the part moved, a parameter or the probe it read changed), or None. Only units whose
    record of what they read exists are judged so: facts measured by an older copy of the gate have no such record
    (`gate.py run --changed` measures them again, and _measured_since() then sees it)."""
    import gate_plan
    entry = (census.get('parts') or {}).get(stamp.get('part')) or {}
    if entry.get('source', 'fusion') != 'fusion' or entry.get('sig') != stamp.get('sig'):
        return None
    probes = gate_common.load_facts(root, stamp['part'], stamp['sig']).get('probes') or {}
    try:
        units = gate_plan.stale_units(root, reg, census=census, parts=[stamp['part']]).get(stamp['part']) or {}
    except GateError as e:
        return 'what the part was measured against cannot be read back (%s)' % e
    import gate_deps
    for probe, why in sorted(units.items()):
        if why and not gate_deps.tool_reason(why) and isinstance(probes.get(probe), dict) and isinstance(probes[probe].get('deps'), dict):
            return '%s: %s' % (probe, why)              # a new probe text or Fusion version measures again (stale_units) but kills nothing
    return None


def _advice(root, stamp):
    """What a stale check of this stamp needs: the stored facts judged again (`run --offline`), or, when facts of the
    stamped geometry hold no record of what they read (measured by an older copy of the gate), measured again first."""
    probes = gate_common.load_facts(root, stamp.get('part'), stamp.get('sig')).get('probes') or {}
    old = [k for k, e in probes.items() if k.startswith('fusion_') and isinstance(e, dict) and not isinstance(e.get('deps'), dict)]
    return 'gate.py run --changed, then export' if old else 'gate.py run --offline, then export'


def _open_findings(root, part, findings):
    """The blocking findings out of `findings` that no waiver, triage decision or answer closes today."""
    import gate_features
    waivers = (gate_common.read_json(gate_common.gate_path(root, 'owner', 'waivers.json'), {}) or {}).get('waivers') or []
    blocking = [f for f in findings or [] if f.get('verdict') in BLOCKING and f.get('part', part) == part]
    still, _w, _r = gate_features.apply_waivers(blocking, waivers, part=part)
    cache = {}
    return [f for f in still if not _closed_by_triage(root, part, f, cache) and not _closed_by_answer(root, part, f, cache)]


def _inputs_moved(root, reg, stamp, entry, resolved, target):
    """Why a check of the stamp no longer passes because the print orientation or the tables it was judged with changed
    (the part turned on its bed in a saved project, placed on a face for the first time, a row of gate/owner/tables.json),
    or None. Only the checks whose stored inputs differ from today's are judged again, from the stored facts, in memory
    (a judgement stored since with today's inputs is taken over); the stamp holds while every one of them still passes."""
    import gate_plan
    held = {rid: c.get('inputs') for rid, c in stamp['checks'].items() if isinstance(c, dict) and isinstance(c.get('inputs'), dict)}
    if not held or not entry:
        return None
    today = gate_plan.judge_inputs(root, reg, stamp['part'], entry)
    moved = {rid: gate_plan.inputs_differ(got, today, reg['tables']) for rid, got in held.items()}
    moved = {rid: ks for rid, ks in moved.items() if ks}
    if not moved:
        return None
    judged = gate_common.load_facts(root, stamp['part'], stamp['sig']).get('judged') or {}
    again = []
    for rid in sorted(moved):
        j = judged.get(rid)
        if isinstance(j, dict) and all(k in j for k in ('print', 'tables')) and not gate_plan.inputs_differ(j, today, reg['tables']) \
                and isinstance(j.get('findings'), list) and j.get('judge_hash') == stamp['checks'][rid].get('judge_hash'):
            if _open_findings(root, stamp['part'], j['findings']):
                return rid, moved[rid], _open_findings(root, stamp['part'], j['findings'])[0]
        else:
            again.append(rid)
    if again:
        try:
            got = gate_plan.judge_part(root, reg, resolved, stamp['part'], sig=stamp['sig'], target=target, rules=set(again), store=False)
        except GateError as e:
            return again[0], moved[again[0]], {'text': 'it cannot be judged again (%s)' % e}
        for rid in again:
            left = _open_findings(root, stamp['part'], got.get(rid))
            if left:
                return rid, moved[rid], left[0]
    return None


def write(root, stamp, supersede=False):
    """Store a stamp at gate_common.stamp_path(). An existing file with the same content is left alone; one with other
    content is a GateError (stamps are never overwritten). Returns the path.

    "The same content" does not look at "time" and "run". supersede=True is for the export that replaces a dead stamp
    of the same signature or adds a second target: the new stamp goes beside the first one as <sig12>.2.json,
    <sig12>.3.json, ... and nothing that exists is touched."""
    missing = [k for k in STAMP_KEYS if k not in stamp]
    if missing:
        raise GateError('a stamp needs %s' % ', '.join(missing))
    if stamp['verdict'] not in VERDICTS:
        raise GateError('a stamp is only written for a pass, got verdict %r' % (stamp['verdict'],))
    if not isinstance(stamp['checks'], dict) or any(not isinstance(c, dict) or c.get('result') not in RESULTS for c in stamp['checks'].values()):
        raise GateError('a stamp\'s checks must each have a result out of %s' % ', '.join(RESULTS))
    path = gate_common.stamp_path(root, stamp['part'], stamp['sig'])
    k = 1
    while os.path.exists(path):
        try:
            old = gate_common.read_json(path)
        except GateError:
            old = None
        if isinstance(old, dict) and _same(old, stamp):
            return path
        if not supersede:
            raise GateError('%s holds another stamp for this signature: stamps are never overwritten' % gate_common.rel(root, path))
        k += 1
        path = '%s.%d.json' % (gate_common.stamp_path(root, stamp['part'], stamp['sig'])[:-len('.json')], k)
    return gate_common.write_json(path, stamp)


def load(root, part, sig):
    """The stamp of that part and signature, or None. When the signature was stamped more than once: the newest."""
    for _path, s in _stamp_files(root, part):
        if s.get('sig') == sig:
            return s
    return None


def head(root, part, census=None):
    """The stamp of the part's newest known signature (census), or None when that geometry has no stamp.
    A part the census does not list: its newest stamp."""
    census = census if census is not None else gate_common.load_census(root)
    entry = (census.get('parts') or {}).get(part)
    if entry and entry.get('sig'):
        return load(root, part, entry['sig'])
    found = _stamp_files(root, part)
    return found[0][1] if found else None


def all_stamps(root, part=None):
    """Every stamp on disk (of one part, or of all), newest first by "time"."""
    return [s for _path, s in _stamp_files(root, part)]


def find_by_geom(root, geom_hash):
    """Stamps whose "geom_hash" equals this one (the model form of a mesh), newest first. [] when none."""
    return [s for s in all_stamps(root) if geom_hash and s.get('geom_hash') == geom_hash]


def verify(root, reg, stamp, geom_hash=None, census=None, target=None, resolved=None):
    """('valid' | 'stale' | 'none', why): is this stamp valid today, for a mesh with that geom_hash (model form)?
    stamp None gives ('none', 'no stamp'). geom_hash None skips the mesh test (the stamp alone is judged).
    The rules are at the top of this file; `why` names the first one that fails.
    target: the declared target of the .3mf project the mesh goes into ({"nozzle", "line_width", "material"}); None
    skips the target test.
    resolved: today's parameters (gate_params.resolve) for the stamp's target; None = read from the project here."""
    import gate_judges
    if stamp is None:
        return 'none', 'no stamp'
    part, sig = stamp.get('part'), stamp.get('sig')
    if not isinstance(part, str) or not sig or stamp.get('verdict') not in VERDICTS or not isinstance(stamp.get('checks'), dict):
        return 'stale', 'the stamp file is not a complete stamp'
    if geom_hash is not None and geom_hash != stamp.get('geom_hash'):
        return 'stale', 'the mesh is not the geometry that was stamped (geom_hash %s, stamped %s)' % (str(geom_hash)[:12], str(stamp.get('geom_hash'))[:12])
    census = census if census is not None else gate_common.load_census(root)
    entry = (census.get('parts') or {}).get(part)
    if entry and entry.get('gone'):
        return 'stale', '%s is gone from its document (census)' % part
    if entry and entry.get('sig') and entry['sig'] != sig:
        return 'stale', 'the part changed since the stamp: signature %s now, %s stamped' % (gate_common.sig12(entry['sig']), gate_common.sig12(sig))
    mine = str(stamp.get('time') or '')
    for other in all_stamps(root, part):
        if other.get('sig') != sig and str(other.get('time') or '') > mine:
            return 'stale', 'not the newest signature of the part: %s was stamped later (%s)' % (gate_common.sig12(other['sig']), other.get('time'))
    since = _measured_since(root, stamp)
    if since:
        return 'stale', since + ': the stamp is about other facts (gate.py run, then export)'
    moved = _neighbours_moved(root, reg, stamp, census)
    if moved:
        return 'stale', 'what the stamped part was checked against changed (%s): that check is open again (gate.py run --changed, then export)' % moved
    if target is not None and _target_key(target) != _target_key(stamp.get('target')):
        return 'stale', 'stamped for another target (%s), this project is declared as %s' % (_target_text(stamp.get('target')), _target_text(target))
    for rid in applicable_rules(reg, {'source': _source(root, part, census, stamp)}):
        check = stamp['checks'].get(rid)
        if not isinstance(check, dict) or check.get('result') not in RESULTS:
            return 'stale', 'block rule %s is not in the stamp: the part was never checked against it' % rid
        name = reg['rules'][rid]['check']['judge']
        try:
            today = gate_judges.judge_hash(name)
        except KeyError:
            return 'stale', 'the judge of %s (%s) does not exist' % (rid, name)
        if check.get('judge_hash') != today:
            return 'stale', 'the judge of %s changed since the stamp: that check is open again (%s)' % (rid, _advice(root, stamp))
        if resolved is None:
            try:
                resolved = _resolved_for(root, reg, _target_today(root, stamp, target))
            except GateError as e:                      # project values that cannot be read: nothing is proven valid
                return 'stale', 'the parameters of the project cannot be read (%s)' % e
        moved = _stricter_params(reg, resolved, reg['rules'][rid], check.get('params'))
        if moved:
            name_, then, now = moved[0]
            return 'stale', ('%s was judged with %s = %s, today it is %s: that check is open again (%s)'
                             % (rid, name_, 'nothing' if then is None else then, now, _advice(root, stamp)))
        if check['result'] != 'pass' and _still_closed(root, part, sig, rid) is False:
            return 'stale', 'the %s result of %s no longer holds: a finding of that check is open again (its waiver, answer or decision is gone)' % (check['result'], rid)
    if entry and entry.get('sig') == sig:
        try:
            got = _inputs_moved(root, reg, stamp, entry, resolved if resolved is not None else _resolved_for(root, reg, _target_today(root, stamp, target)),
                                _target_today(root, stamp, target))
        except GateError as e:
            got = (None, ['print'], {'text': 'the print orientation cannot be worked out (%s)' % e})
        if got:
            rid, what, f = got
            return 'stale', ('the %s that %s was judged with changed since the stamp, and today it does not pass: %s (gate.py run --offline, then export)'
                             % (' and the '.join({'print': 'print orientation', 'tables': 'tables'}[k] for k in what), rid or 'a check', f.get('text') or f.get('verdict')))
    if stamp.get('answers'):
        import gate_attest
        gone = gate_attest.answers_gone(root, part, stamp['answers'], reg)
        if gone:
            return 'stale', ('%d answer(s) this stamp rests on are gone (%s): the waiver behind an answer was withdrawn, the answer was removed, or the facts of the part contradict it today. '
                             'gate.py attest list shows the question again' % (len(gone), ', '.join(gone[:4])))
    return 'valid', 'stamp %s (%s)' % (name_of(stamp), stamp['verdict'])


def _valid_file(root, reg, part, sig, census=None, target=None, geom_hash=None):
    """Path of a stamp of this part and signature that is valid today (for that target and geometry), or None."""
    for path, s in _stamp_files(root, part):
        if s.get('sig') == sig and verify(root, reg, s, geom_hash=geom_hash, census=census, target=target)[0] == 'valid':
            return path
    return None


# ---- export ----------------------------------------------------------------------------------------------------------

def promote(quarantine_stl, dest_stl):
    """Move a checked STL from quarantine into the STL folder (os.replace; the folder is made when missing).
    Returns the destination path."""
    os.makedirs(os.path.dirname(os.path.abspath(dest_stl)), exist_ok=True)
    try:
        os.replace(quarantine_stl, dest_stl)
    except OSError:                                     # another volume: copy beside the destination, then replace
        import shutil
        tmp = '%s.tmp%d' % (dest_stl, os.getpid())
        shutil.copyfile(quarantine_stl, tmp); os.replace(tmp, dest_stl); os.remove(quarantine_stl)
    return dest_stl


def _result(part):
    return {'part': part, 'exit': EXIT_INFRA, 'stamp': None, 'stl': None, 'quarantine': None, 'open': [], 'questions': [], 'why': None, 'unit': False,
            'note': None}


def _holds_stamped_bytes(root, part, sig, path):
    """Is this file, byte for byte, the STL of a stamp of this part and signature ("stl_sha256")? `gate.py status` reads
    an STL by that test, so "nothing to do" may only be said of such a file."""
    try:
        sha = gate_common.sha256_file(path)
    except OSError:
        return False
    return any(s.get('sig') == sig and s.get('stl_sha256') == sha for _path, s in _stamp_files(root, part))


def _stop(out, code, why, **more):
    out.update(exit=code, why=why, **more)
    return out


def _host_envelope(part, sig, probe, facts, elapsed, error=None):
    return {'job': None, 'doc': None, 'doc_version': None, 'part': part, 'sig': sig, 'probe': probe, 'facts': facts,
            'done': error is None, 'cursor': None, 'elapsed_s': round(elapsed, 2), 'error': error}


def _measure_stl(root, reg, resolved, part, sig, stl):
    """Run the host probes on one STL and store their facts. A probe that cannot measure stores nothing: its rules are
    then NOT_RUN, never a pass. Returns the stl_mesh facts or None."""
    import gate_params
    from probes import stl_mesh, stl_walls
    t = time.time(); mesh = None
    try:
        mesh = stl_mesh.measure(stl)
        gate_common.save_probe_facts(root, part, sig, _host_envelope(part, sig, 'stl_mesh', mesh, time.time() - t))
    except GateError:
        return None
    vals = gate_params.values(resolved)
    under = 2 * max(float(vals.get('walls.min_wall_effective') or 0), 2 * float(vals.get('target.line_width') or 0), float(vals.get('walls.slit_min') or 0))
    if not mesh.get('open_edges') and not mesh.get('non_manifold_edges'):
        t = time.time()
        try:
            walls = stl_walls.measure(stl, under, budget_s=60.0, seed=1)
            gate_common.save_probe_facts(root, part, sig, _host_envelope(part, sig, 'stl_walls', walls, time.time() - t))
        except GateError:
            pass
    return mesh


def _verdict_stop(out, v):
    """Fill `out` from a part verdict that is not a pass. Returns True when the export stops here."""
    verdict = v.get('verdict')
    if verdict in VERDICTS:
        return False
    out['open'] = list(v.get('open') or [])
    out['questions'] = list(v.get('asked') or []) + list(v.get('questions') or [])
    if verdict == 'attest':
        _stop(out, EXIT_ATTEST, '%d question(s) open: gate.py attest list' % len(out['questions']))
    elif verdict == 'unreachable':
        _stop(out, EXIT_INFRA, 'the document of this part is not open in Fusion')
    elif verdict == 'incomplete':
        _stop(out, EXIT_BLOCK, 'not every check ran on this geometry (%s): gate.py run --part %s' % (', '.join(v.get('not_run') or []) or 'see the report', out['part']))
    else:
        _stop(out, EXIT_BLOCK, '%d open finding(s): gate.py report --last' % len(out['open']))
    return True


def _finish(root, reg, resolved, out, part, sig, stl, dest, target, stages, doc_version, run, target_key=None, intent=None):
    """Judge the export stage, and when everything passes build the stamp, promote and write. Fills and returns `out`.

    Everything is judged against the geometry being exported (`stl`, in quarantine; gate_plan.exporting): the face it
    lies on is the face of the placement, applied to this geometry. A project that still places the STL in the folder
    (an older export, or an STL of an older exporter while the model changed since) gives "earlier geometry": the
    design rules that read the print frame are judged again here with it, print.bed_face is asked, not answered by the
    facts, and the stamp holds the frame the checks were judged with. So export, verify and the save that swaps the
    new mesh in agree."""
    import gate_plan
    with gate_plan.exporting(part, stl if os.path.abspath(stl) != os.path.abspath(dest) else None):
        if 'design' in stages:
            gate_plan.judge_part(root, reg, resolved, part, sig, stage='design', target=target, intent=intent)
        gate_plan.judge_part(root, reg, resolved, part, sig, stage='export', target=target)
        v = gate_plan.part_verdict(root, reg, part, sig, stages=stages)
        if _verdict_stop(out, v):
            return out
        judged = gate_common.load_facts(root, part, sig)['judged']
        try:
            stamp = build(root, reg, resolved, part, sig, judged, stl, target, attested=v.get('attested') or 0, doc_version=doc_version, run=run,
                          target_key=target_key, answers=[q['q'] for q in v.get('all_questions') or [] if isinstance(q, dict) and q.get('answer') and q.get('q')])
        except GateError as e:                          # the stored judgement and the verdict disagree: nothing is promoted
            return _stop(out, EXIT_INFRA, str(e))
    stamp['stl'] = gate_common.rel(root, dest)
    if os.path.abspath(stl) != os.path.abspath(dest):
        promote(stl, dest)
    out['stamp'] = write(root, stamp, supersede=True)
    return _stop(out, EXIT_PASS, None, stl=dest)


def _signature_now(root, reg, resolved, layout, doc, doc_key, part, entry, project, transport, pinger):
    """(the part's B-rep signature as Fusion has it now, None) or (None, why it could not be read): one short read-only
    unit (fusion_thin with args {"sig_only": true}: it reads the body and measures nothing)."""
    import gate_fusion_rpc, gate_params
    run = gate_common.new_run_id()
    job = gate_fusion_rpc.new_job('u-sig', run, 'fusion_thin', doc.get('name'), doc_key,
                                  os.path.join(gate_common.run_dir(root, run), 'units', 'u-sig.json'), part=part,
                                  component_id=entry.get('component_id'), occurrence=entry.get('occurrence'), body=entry.get('body'),
                                  sig=entry.get('sig'), deadline_s=min(10.0, gate_fusion_rpc.budget(layout)), params={}, tables={},
                                  not_printed=project.get('not_printed'), args={'sig_only': True})
    try:
        env = gate_fusion_rpc.run_unit(job, gate_fusion_rpc.endpoint(layout), transport=transport, pinger=pinger)
    except gate_fusion_rpc.FusionDown as e:
        return None, 'Fusion does not answer: %s' % e
    if env.get('error'):
        return None, str(env['error']).strip().splitlines()[0][:200]
    if not env.get('sig'):
        return None, 'the unit returned no signature'
    return env['sig'], None


def _fusion_origin(root, stl, census, layout):
    """Why an STL cannot be stamped as a part made by code, or None: it is the export file of a Fusion part of the
    census, its header says a Fusion exporter wrote it, or it has the geometry of a stamped Fusion part."""
    real = lambda p: os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
    here = real(stl)
    for pid, e in sorted((census.get('parts') or {}).items()):
        if e.get('source') == 'generated':
            continue
        path = e.get('stl')                             # the census names the file; the layout is asked only for an entry without one
        if not path:
            try:
                doc_key, slug = gate_common.split_part_id(pid)
            except GateError:
                continue
            try:
                path = gate_common.stl_of(gate_common.document_entry(layout, doc_key), e.get('name') or slug)
            except GateError:                           # the document left gate/layout.json: the part's default file is still its file
                path = None
            path = path or 'stl/%s.stl' % slug
        if real(path) == here:
            return 'it is the export file of the Fusion part %s: export that part with `gate.py export %s`' % (pid, pid)
    try:
        with open(stl, 'rb') as f:
            head_ = f.read(80)
    except OSError:
        head_ = b''
    if head_[:7] == b'Fusion ':
        return 'its header says it was exported from Fusion (%r): a Fusion part is checked and exported with `gate.py export PART`' % head_.decode('ascii', 'replace').strip()
    try:
        geom = gate_common.sibling('meshid').stl_ids(stl)['geom_hash']
    except (ValueError, OSError):
        return None
    for s_ in all_stamps(root):
        e = (census.get('parts') or {}).get(s_.get('part')) or {}
        if s_.get('geom_hash') == geom and e and e.get('source') != 'generated':
            return 'it has the geometry of the Fusion part %s' % s_.get('part')
    return None


def scan_for_export(root, doc_key, wall_s=20.0, transport=None, pinger=None, show=False):
    """Scan the document of a part before it is exported: the census, and with it every neighbour and ref the part's
    units read, is then the model as it is, not as the last scan saw it. Returns None, or (exit code, why) when the
    export cannot go on: Fusion does not answer or the document is not open (4), the scan needs another call (3).
    show: print what the scan found (gate_plan._print_scan: new and changed parts, STL paths that moved, ...)."""
    import gate_plan, gate_fusion_rpc
    try:
        res = gate_plan.scan(root, doc_key, wall_s=20.0 if wall_s is None else max(float(wall_s), 1.0), transport=transport, pinger=pinger)
    except gate_fusion_rpc.FusionDown as e:
        return EXIT_INFRA, 'Fusion does not answer, so what the part was checked against cannot be confirmed: %s' % e
    if show:
        gate_plan._print_scan(res, gate_plan._hint_faces(root))
    if res.get('errors'):
        return EXIT_INFRA, 'the scan before the export failed: %s' % '; '.join('%s: %s' % kv for kv in sorted(res['errors'].items()))
    if doc_key in (res.get('unreachable') or []):
        return EXIT_INFRA, 'the document of the part is not open in Fusion: what it was checked against cannot be confirmed (open it, then export)'
    if not res.get('done'):
        return EXIT_MORE, 'the scan before the export needs another call: call again'
    return None


def _checked_against(root, reg, resolved, part, census, target_key):
    """Why the facts the part's verdict rests on no longer hold for the model the census has (gate_plan.stale_units:
    a unit of the part whose part, neighbour, ref, parameter or probe changed since it was measured, or that has no
    record of what it read), or None."""
    import gate_plan
    try:
        units = gate_plan.stale_units(root, reg, resolved, census, parts=[part], target_key=target_key).get(part) or {}
    except GateError as e:
        return 'what the part was measured against cannot be read back (%s)' % e
    for probe, why in sorted(units.items()):
        if why:
            return '%s: %s' % (probe, why)
    return None


def _kept_export(root, part, sig, quarantine, job_key):
    """The stored fusion_export entry of this signature when the STL it wrote is still in quarantine, byte for byte, and
    was written for the same job (parameters, tables, target, body): an export that stopped at a question or a finding
    left it there. None otherwise (the export unit runs again)."""
    if not os.path.isfile(quarantine):
        return None
    e = (gate_common.load_facts(root, part, sig).get('probes') or {}).get('fusion_export')
    q = e.get('quarantine') if isinstance(e, dict) else None
    if not isinstance(q, dict) or e.get('error') or not e.get('done', True) or not (e.get('facts') or {}).get('written'):
        return None
    if q.get('job') != job_key or q.get('sha256') != gate_common.sha256_file(quarantine):
        return None
    return e


def export_part(root, reg, resolved, part, target_key=None, wall_s=20.0, transport=None, pinger=None, scanned=False):
    """Export one part through quarantine as described at the top. Returns {"part", "exit": 0 | 1 | 2 | 3 | 4,
    "stamp": path or None, "stl": promoted path or None, "quarantine": path or None, "open": [findings],
    "questions": [open questions], "why": text when nothing was promoted, "unit": True when the export unit was sent
    to Fusion in this call, "note": one line when gate/layout.json gives the part another STL path than the census of
    the last scan (the census path is the one written), else None}. wall_s: the time left in this call; None = the
    unit starts whatever the clock says.

    "Already stamped: nothing to do" needs a valid stamp of this signature AND a file in the folder that holds exactly
    the bytes of a stamp of this signature. A file with the stamped geometry in other bytes (the same mesh written by
    another tool, an older export under another header) is exported again: the file is replaced and the next stamp
    is written, so `gate.py status` and `export` say the same about it.

    Before anything is judged the document of the part is scanned (scan_for_export; scanned=True: the caller did it
    in this call, cmd_export scans each document once), and the export is refused (exit 1) while a unit of the part
    must be measured again because what it read changed (_checked_against): a neighbour that moved since the run can
    close a hole of the part, and its stamp would say otherwise. That holds for "already stamped" too."""
    import gate_plan, gate_report, gate_fusion_rpc, gate_params
    out = _result(part)
    if not scanned:
        try:
            doc_of = gate_common.split_part_id(part)[0]
        except GateError as e:
            return _stop(out, EXIT_INFRA, str(e))
        if doc_of in {d.get('key') for d in gate_common.load_layout(root).get('documents') or []}:
            stop = scan_for_export(root, doc_of, wall_s, transport, pinger)
            if stop:
                return _stop(out, stop[0], stop[1])
    census = gate_common.load_census(root)
    entry = (census.get('parts') or {}).get(part)
    if not entry or entry.get('gone') or not entry.get('sig'):
        return _stop(out, EXIT_INFRA, 'unknown part (not in gate/census.json, or gone): gate.py scan')
    if entry.get('source') == 'generated':
        return _stop(out, EXIT_INFRA, 'a part made by code: gate.py export --generated %s' % (entry.get('stl') or 'STL'))
    sig = entry['sig']; doc_key, slug = gate_common.split_part_id(part)
    project = gate_common.load_project(root)
    target = gate_common.target_of(project, target_key)
    if target is None and project.get('targets'):
        return _stop(out, EXIT_INFRA, 'several targets are declared: name one with --for KEY')
    target_key = gate_common.target_key_of(project, target_key)
    last = gate_plan.load_plan(root) or {}
    intent = last.get('intent') if last.get('command') == 'run' else None
    if gate_common.load_facts(root, part, sig).get('judged'):
        # the stored design judgement may be the one of another target (or of no target): the facts are the same, so
        # judge them again for THIS target before the verdict is read. A part no run has looked at stays "incomplete".
        gate_plan.judge_part(root, reg, resolved, part, sig, stage='design', target=target, intent=intent)
    if _verdict_stop(out, gate_plan.part_verdict(root, reg, part, sig, stages=('design',))):
        return out
    inherited = gate_report.inherited_block(root, part)
    if inherited:
        return _stop(out, EXIT_BLOCK, 'inherited findings wait for the owner\'s decision or a fix: %s (gate.py triage)'
                     % ', '.join(str(i.get('id')) for i in inherited), inherited=inherited)
    moved = _checked_against(root, reg, resolved, part, census, target_key)
    if moved:
        return _stop(out, EXIT_BLOCK, 'what the part was checked against changed since it was measured (%s): `gate.py run --changed --for KEY` '
                                      'measures it again, then export' % moved)
    layout = gate_common.load_layout(root); doc = gate_common.document_entry(layout, doc_key)
    now_stl = gate_common.stl_of(doc, entry.get('name') or slug)
    dest = os.path.join(root, entry.get('stl') or now_stl or 'stl/%s.stl' % slug)     # the census names the file (the scan read "stl_names")
    if entry.get('stl') and now_stl and now_stl != entry['stl']:
        out['note'] = ('gate/layout.json gives this part the file %s now; the census of the last scan says %s, and that is the file written. '
                       '`gate.py scan` makes the change take effect, then export again' % (now_stl, entry['stl']))
    if os.path.isfile(dest):                            # stamped already, for this target, and the file in the folder is that geometry
        try:
            have = gate_common.sibling('meshid').stl_ids(dest)['geom_hash']
        except (ValueError, OSError):
            have = None
        old = _valid_file(root, reg, part, sig, census, target, have) if have else None
        if old and not _holds_stamped_bytes(root, part, sig, dest):
            old = None                                  # the stamped geometry in other bytes: export it, so the file and a stamp agree
        if old:
            # the census is only as new as the last scan: ask Fusion for the signature as it is now before saying "nothing to do"
            now_sig, why = _signature_now(root, reg, resolved, layout, doc, doc_key, part, entry, project, transport, pinger)
            if now_sig is None:
                return _stop(out, EXIT_INFRA, 'a stamp exists for the geometry of the last scan, but it could not be confirmed that the part '
                                              'is unchanged (%s): nothing was exported' % why)
            if now_sig != sig:
                return _stop(out, EXIT_BLOCK, 'the part changed since its stamp (signature %s now, %s stamped): the STL in the folder is the OLD '
                                              'geometry. gate.py scan, then run, then export' % (gate_common.sig12(now_sig), gate_common.sig12(sig)))
            out['stamp'] = old
            return _stop(out, EXIT_PASS, 'already stamped, and Fusion confirms the part is unchanged: nothing to do', stl=dest)
    budget = gate_fusion_rpc.budget(layout)
    run = gate_common.new_run_id()
    quarantine = gate_common.gate_path(root, 'run', 'quarantine', '%s__%s.stl' % (doc_key, slug))
    job_key = gate_common.digest({'params': gate_params.values(resolved), 'tables': gate_plan._probe_tables(reg), 'target': target,
                                  'doc': doc.get('name'), 'component_id': entry.get('component_id'), 'occurrence': entry.get('occurrence'),
                                  'body': entry.get('body'), 'name': entry.get('name') or slug, 'not_printed': project.get('not_printed'),
                                  'motion_groups': doc.get('motion_groups')})
    kept = _kept_export(root, part, sig, quarantine, job_key)
    if kept is not None:
        # the STL an earlier call of this export wrote for this very signature and job, still in quarantine (it stopped at
        # a question): judged again as it is, with no second export from Fusion
        out['quarantine'], out['reused'] = quarantine, True
        _measure_stl(root, reg, resolved, part, sig, quarantine)
        return _finish(root, reg, resolved, out, part, sig, quarantine, dest, target, ('design', 'export'), kept.get('doc_version'), run, target_key, intent)
    if wall_s is not None and wall_s < budget:
        return _stop(out, EXIT_MORE, 'no time left in this call for the export unit: call again')
    os.makedirs(os.path.dirname(quarantine), exist_ok=True)
    if os.path.exists(quarantine):
        os.remove(quarantine)
    out['quarantine'] = quarantine
    job = gate_fusion_rpc.new_job('u-0001', run, 'fusion_export', doc.get('name'), doc_key,
                                  os.path.join(gate_common.run_dir(root, run), 'units', 'u-0001.json'), part=part,
                                  component_id=entry.get('component_id'), occurrence=entry.get('occurrence'), body=entry.get('body'),
                                  sig=sig, deadline_s=budget, params=gate_params.values(resolved), tables=gate_plan._probe_tables(reg), target=target,
                                  not_printed=project.get('not_printed'), motion_groups=doc.get('motion_groups'),
                                  args={'stl': quarantine, 'name': entry.get('name') or slug})
    os.makedirs(os.path.dirname(job['out']), exist_ok=True)
    out['unit'] = True
    try:
        env = gate_fusion_rpc.run_unit(job, gate_fusion_rpc.endpoint(layout), transport=transport, pinger=pinger)
    except gate_fusion_rpc.FusionDown as e:
        return _stop(out, EXIT_INFRA, 'Fusion does not answer: %s' % e)
    facts = env.get('facts') or {}
    if env.get('error'):
        return _stop(out, EXIT_INFRA, 'the export unit failed: %s' % env['error'])
    if not env.get('done'):
        return _stop(out, EXIT_MORE, 'the export unit did not finish: call again')
    if env.get('sig') and env['sig'] != sig:
        return _stop(out, EXIT_BLOCK, 'the part changed while it was exported (signature %s, checked %s): gate.py scan, then run'
                     % (gate_common.sig12(env['sig']), gate_common.sig12(sig)))
    if not facts.get('written') or not os.path.isfile(quarantine):
        return _stop(out, EXIT_BLOCK, 'no STL was written: %s' % (facts.get('why_not') or 'the mesh failed its checks'))
    gate_common.save_probe_facts(root, part, sig, dict(env, part=part, sig=sig, probe='fusion_export',
                                                       quarantine={'job': job_key, 'sha256': gate_common.sha256_file(quarantine)}))
    _measure_stl(root, reg, resolved, part, sig, quarantine)
    return _finish(root, reg, resolved, out, part, sig, quarantine, dest, target, ('design', 'export'), env.get('doc_version'), run, target_key, intent)


def export_generated(root, reg, resolved, stl_path, target_key=None):
    """Stamp an STL made by code: it must belong to a generator of gate/layout.json (that entry names the part id) and
    must NOT come from Fusion (_fusion_origin: the export file of a census part, a Fusion exporter's header, or the
    geometry of a stamped Fusion part): such a part has design rules that only `gate.py export PART` checks;
    stl_mesh and stl_walls measure it where it lies, the export-stage judges and questions (generated.source) run, then
    the stamp is written with "sig" = its geom_hash. Returns the dict of export_part()."""
    stl = stl_path if os.path.isabs(stl_path) else os.path.join(root, stl_path)
    if not os.path.isfile(stl) and os.path.isfile(os.path.abspath(stl_path)):
        stl = os.path.abspath(stl_path)
    layout = gate_common.load_layout(root)
    real = lambda p: os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
    gen = [g for g in layout['generators'] if g.get('stl') and real(g['stl']) == real(stl)]
    out = _result(gen[0].get('part') if gen else stl_path)
    if not gen:
        return _stop(out, EXIT_INFRA, '%s is not the STL of a generator in gate/layout.json' % stl_path)
    part = gen[0]['part']; gate_common.split_part_id(part)
    if not os.path.isfile(stl):
        return _stop(out, EXIT_INFRA, 'the STL %s does not exist: run its generator (%s)' % (stl_path, gen[0].get('source')))
    fusion = _fusion_origin(root, stl, gate_common.load_census(root), layout)
    if fusion:                                          # a generator entry must not stamp a Fusion part with the export rules only
        return _stop(out, EXIT_BLOCK, '%s is not a part made by code: %s. A generator is a script that computes the mesh itself '
                                      '(no Fusion body behind it); remove the entry from "generators" in gate/layout.json' % (stl_path, fusion))
    project = gate_common.load_project(root)
    target = gate_common.target_of(project, target_key)
    if target is None and project.get('targets'):
        return _stop(out, EXIT_INFRA, 'several targets are declared: name one with --for KEY')
    target_key = gate_common.target_key_of(project, target_key)
    try:
        sig = gate_common.sibling('meshid').stl_ids(stl)['geom_hash']
    except ValueError as e:
        return _stop(out, EXIT_INFRA, str(e))
    old = _valid_file(root, reg, part, sig, None, target, sig)
    if old:
        out['stamp'] = old
        return _stop(out, EXIT_PASS, 'already stamped: nothing to do', stl=stl)
    _measure_stl(root, reg, resolved, part, sig, stl)
    return _finish(root, reg, resolved, out, part, sig, stl, stl, target, ('export',), None, gate_common.new_run_id(), target_key)


def cmd_export(root, args):
    """gate.py export PART [...] [--for KEY] [--wall 20]  |  gate.py export --generated STL [--for KEY].
    args.parts: part ids; args.generated: path of an STL made by code; args.target: key of the .3mf project whose
    declared target the part is judged for (default: the only declared target); args.wall: seconds of this call.
    Returns 0 every part stamped, 1 a part has open block findings (nothing promoted for it), 2 questions open,
    3 more calls needed, 4 infrastructure (Fusion down, document not open, unknown part).

    Several parts in one call: the first export unit of a call always starts. The clock runs from there; a later part
    whose unit would not fit into what is left of args.wall waits for the next call (exit 3, `NEXT n/m`). A part that
    is stamped already costs one short Fusion call and no time of the wall, so the next call reaches the next part.
    Every call writes a report (gate/reports/<run>.md, command "export") with the findings and the answers of both
    stages of the parts it was asked for."""
    import gate_registry, gate_params, gate_plan
    parts, generated = list(dict.fromkeys(args.parts or [])), args.generated
    if bool(parts) == bool(generated):
        raise GateError('export needs part ids, or --generated STL (one of the two)')
    reg = gate_registry.load_default(root)
    project = gate_common.load_project(root)
    target = gate_common.target_of(project, args.target)
    if target is None and project.get('targets'):
        raise GateError('gate/project.json declares several targets: name one with --for KEY (%s)' % ', '.join(sorted(project['targets'])))
    resolved = gate_params.resolve(reg, gate_params.load_overrides(root), target)
    wall = getattr(args, 'wall', None)
    wall = 20.0 if wall is None else float(wall)
    results, t0 = [], None                              # t0: when the first export unit of this call was sent
    if generated:
        results.append(export_generated(root, reg, resolved, generated, args.target))
    else:
        gate_plan.acquire_lock(root)
        try:
            # one scan per document of the named parts, first: the export judges against the model as it is now
            registered = {d.get('key') for d in gate_common.load_layout(root).get('documents') or []}
            stops = {}
            for doc_key in sorted({gate_common.split_part_id(p)[0] for p in parts if '/' in p} & registered):
                stops[doc_key] = scan_for_export(root, doc_key, wall, show=True)
            for part in parts:
                began = time.time()
                stop = stops.get(part.split('/', 1)[0])
                if stop:
                    results.append(_stop(_result(part), stop[0], stop[1]))
                    continue
                r = export_part(root, reg, resolved, part, args.target, wall_s=None if t0 is None else wall - (time.time() - t0), scanned=True)
                if r.get('unit') and t0 is None:
                    t0 = began
                results.append(r)
        finally:
            gate_plan.release_lock(root)
    for r in results:
        if r['exit'] == EXIT_PASS:
            print('EXPORT %-28s stamped -> %s%s' % (r['part'], gate_common.rel(root, r['stl']), '  (%s)' % r['why'] if r['why'] else ''))
            if r.get('note'):
                print('    NOTE %s' % r['note'])
        else:
            print('EXPORT %-28s NOT exported (exit %d): %s' % (r['part'], r['exit'], r['why']))
            for f in r['open'][:10]:
                print('    %s %s: %s' % (f.get('verdict'), f.get('rule'), f.get('text')))
            for q in r['questions'][:10]:
                print('    ? %s %s' % (q.get('q') or q.get('rule'), q.get('question') or q.get('text') or ''))
    codes = [r['exit'] for r in results]
    code = next((c for c in (EXIT_INFRA, EXIT_MORE, EXIT_BLOCK, EXIT_ATTEST) if c in codes), EXIT_PASS)
    _export_report(root, reg, resolved, [r['part'] for r in results], code)
    if code == EXIT_MORE:
        print('NEXT %d/%d' % (sum(1 for c in codes if c != EXIT_MORE), len(codes)))
    return code


def _export_report(root, reg, resolved, parts, code):
    """Write the report of one export call, so the answers of the export questions reach a report: the verdict of
    both stages for every part it named, and beside them every other part the report before it listed (a report is
    what `gate.py report --last` shows: an export must not make the open rows of the other parts vanish from it).
    Never the reason an export fails: trouble here is one line."""
    try:
        import gate_plan, gate_registry, gate_report
        census = gate_common.load_census(root)
        export_rules = {r['id'] for r in gate_registry.rules(reg, stage='export')}
        before = gate_report.load_latest(root) or {}
        listed = [p.get('part') for p in before.get('parts') or [] if isinstance(p, dict)]
        verdicts = []
        for part in list(dict.fromkeys(list(parts) + listed)):
            entry = (census.get('parts') or {}).get(part) if isinstance(part, str) else None
            if not entry or entry.get('gone') or not entry.get('sig'):
                continue
            judged = gate_common.load_facts(root, part, entry['sig']).get('judged') or {}
            if entry.get('source') == 'generated':
                stages = ('export',)
            else:
                stages = ('design', 'export') if (part in parts or export_rules & set(judged)) else ('design',)
            verdicts.append(gate_plan.part_verdict(root, reg, part, stages=stages))
        if not verdicts:
            return None
        run = gate_common.new_run_id()
        report = gate_report.build(root, reg, resolved, run, 'export', 'export', verdicts)
        report['exit'] = code
        gate_report.write(root, report)
        print('report: gate/reports/%s.md' % run)
        return run
    except NotImplementedError:
        return None
    except Exception as e:
        print('the report of this export was not written (%s: %s)' % (type(e).__name__, e))
        return None
