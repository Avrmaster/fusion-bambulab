"""The plan of a run: which units to run on which parts, where each one stopped, and the sum of what they found.

One unit = one probe on one part (fusion_scan: on one document). A plan is a file, gate/run/<run>/plan.json, so a run
can stop after `--wall` seconds (exit 3, `NEXT n/m`) and the next call goes on where it stopped:

    {"run": "20310203T101207Z-3fa2", "command": "run" | "scan" | "export", "created": "...", "audit": false,
     "intent": ["demo/motor_plate"] or null, "parts": ["demo/motor_plate", ...],
     "units": [{"id": "u-0001", "doc": "demo", "part": "demo/motor_plate" or null, "sig": "..." or null,
                "probe": "fusion_census", "state": "todo" | "partial" | "done" | "error" | "skipped",
                "cursor": null, "attempts": 0, "budget_s": 20, "error": null,
                "out": "gate/run/<run>/units/u-0001.json"}]}

gate/run/current.json = {"run": id} names the plan a call without arguments continues. gate/run/lock = {"pid", "time"}
is held while units run: one run at a time per project.

Order of the units of one part: PROBE_ORDER. fusion_census comes first: the units after it get its holes and openings in
JOB["features"]. A unit whose probe ended in an error, or never ran, leaves its rules NOT_RUN: not passed.

The verdict of a part is never read from one call's output. It is computed from the stored facts of ALL its units at the
part's current signature (gate/facts/<doc>/<part>/<sig12>.json), judged with today's judges and parameters, minus the
valid waivers, minus the findings an attest answer closes.

What this file adds to the plan beyond the keys above (all optional for a reader):
    plan      "request" (what the command line asked for: a later call with the same request continues the plan),
              "unreachable" (document keys that were not open), "changed_under_run" (part ids whose body changed
              between the scan and a unit)
    unit      "failures" (calls in a row that ended in an error or made no progress; MAX_ATTEMPTS of them end the unit),
              "timeouts" (calls in a row that got no answer in time and left no result; MAX_TIMEOUTS of them end the
              unit as "over budget, not run", and the run goes on with the next unit),
              "flight" ({"sent", "cursor", "counted"}: the call of this unit that was sent and whose answer was not
              processed yet. Written before the call goes out, removed when its answer was processed: the unit finds
              the result file of that call whatever happened to gate/run/pending.json in between),
              "over" ([{"call", "elapsed_s", "budget_s", "over_s", "late"}]: every call of the unit that worked longer
              than its budget; "late": its answer came after the host had stopped waiting and was read from the
              result file by the next call; and [{"call", "budget_s", "no_answer": true, "waited_s", "why"}]: a call
              that got no answer in time and left no result), "over_budget": "not run" (the unit was given up)
A unit that runs over its budget is a defect of its probe: over_budget() lists them, the report of the run, `gate.py
scan`, `gate.py status` and `gate.py doctor` print one line each, and the facts file of the unit has the line as a note.
Files of a run besides plan.json: units/<id>.json (the envelope of the unit's last call) and units/<id>.partial.json
(the merged facts of a unit that needs more calls). gate/run/scan.json names the plan `scan` continues.

A new `run` scans first (signatures, poses and every body of the document are read from Fusion, not from an old census).
A unit is measured only when its stored facts no longer hold for the model the scan found (stale_units, gate_deps.py:
its part, a body in a region it read, a parameter, table or job key it read, or its probe changed); the others are
taken over as done. `--no-scan` uses the census as it is, `--fresh` measures every unit of the chosen parts."""
import contextlib, hashlib, json, os, time
import gate_common, gate_deps, gate_fusion_rpc, gate_params, gate_registry
from gate_common import (GateError, EXIT_PASS, EXIT_BLOCK, EXIT_ATTEST, EXIT_MORE, EXIT_INFRA, gate_path, load_census, load_facts,
                         load_layout, load_project, read_json, write_json)

PROBE_ORDER = ('fusion_census', 'fusion_inserts', 'fusion_openings', 'fusion_screws', 'fusion_bearings', 'fusion_gap', 'fusion_thin', 'fusion_near')
STATES = ('todo', 'partial', 'done', 'error', 'skipped')
MAX_ATTEMPTS = 2
MAX_TIMEOUTS = 2                # calls in a row with no answer in time and no result: the unit is given up, the run goes on
OVER_BUDGET = gate_deps.GIVEN_UP                 # 'over budget, not run': how the error of such a unit begins (the judges say it in their NOT_RUN row)
NEVER_RAN = 'the host has stopped waiting'      # in the error of a unit that started too late to measure (probes/_fusion.py run())
NEEDS_CENSUS = ('fusion_inserts', 'fusion_openings', 'fusion_screws', 'fusion_bearings')    # probes that read JOB["features"]
WALL_GRACE_S = 0.5              # a unit still starts when this little of its budget is missing from the wall time
PACK_FACTOR, PACK_MARGIN_S = 1.5, 1.5   # a unit whose last measurement took t s starts in what is left of a call when 1.5 t + 1.5 s fit
PACK_MIN_S = 2.0                # ... and never with less than this left: its deadline is what is left, less the margin
LOCK_MAX_AGE_S = 600
BATCH_UNIT_S = 3.0              # a unit whose last measurement took at most this long may share one Fusion call with others of its part
BATCH_MAX = 8                   # units in one batched call at most
NOT_OPEN = 'document not open'

_clock = time.monotonic         # the tests replace it with the clock of the fake Fusion
_LOCKS = {}                     # lock path -> how often this process took it


# ---- the lock ------------------------------------------------------------------------------------------------------------

def _lock_path(root):
    return gate_path(root, 'run', 'lock')


def _pid_alive(pid):
    if os.name == 'nt':                     # os.kill(pid, 0) ends the process on Windows: only the age rule applies there
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (OSError, ValueError, TypeError):
        return True
    return True


def acquire_lock(root):
    """Take gate/run/lock for this process. GateError when another live process holds it (a lock of a dead pid, or one
    older than 10 minutes, is taken over)."""
    path = _lock_path(root)
    if _LOCKS.get(path):
        _LOCKS[path] += 1
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(path, encoding='utf-8') as f:
                    held = json.load(f)
                age = time.time() - os.path.getmtime(path)
            except (ValueError, OSError):
                held, age = {}, LOCK_MAX_AGE_S + 1
            pid = held.get('pid') if isinstance(held, dict) else None
            if pid is not None and pid != os.getpid() and age <= LOCK_MAX_AGE_S and _pid_alive(pid):
                raise GateError('another gate run holds gate/run/lock (pid %s since %s): one run at a time per project' % (pid, held.get('time')))
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'pid': os.getpid(), 'time': gate_common.now()}, f)
        _LOCKS[path] = 1
        return path
    raise GateError('cannot take gate/run/lock')


def release_lock(root):
    """Give the lock back when this process holds it."""
    path = _lock_path(root)
    if not _LOCKS.get(path):
        return
    _LOCKS[path] -= 1
    if _LOCKS[path] > 0:
        return
    del _LOCKS[path]
    try:
        with open(path, encoding='utf-8') as f:
            held = json.load(f)
        if held.get('pid') == os.getpid():
            os.remove(path)
    except (ValueError, OSError, AttributeError):
        pass


def _beat(root):
    """Renew the age of the lock: a long run in the background stays the holder."""
    try:
        os.utime(_lock_path(root), None)
    except OSError:
        pass


# ---- plans ---------------------------------------------------------------------------------------------------------------

def _finished(unit):
    return unit['state'] in ('done', 'error', 'skipped')


def progress(plan):
    """(n, m): units that are finished (done, error after MAX_ATTEMPTS, skipped) and all units."""
    units = plan.get('units') or []
    return sum(1 for u in units if _finished(u)), len(units)


def save_plan(root, plan):
    """Write gate/run/<run>/plan.json. Returns the path."""
    return write_json(os.path.join(gate_common.run_dir(root, plan['run']), 'plan.json'), plan)


def load_plan(root, run=None):
    """The plan of that run; with no run, the one gate/run/current.json names. None when there is none."""
    if run is None:
        run = (read_json(gate_path(root, 'run', 'current.json'), None) or {}).get('run')
        if not run:
            return None
    return read_json(os.path.join(gate_common.run_dir(root, run), 'plan.json'), None)


def _rule_budget(reg, probe):
    """The largest budget_s a rule sets for this probe in its check, or None."""
    got = [r['check']['budget_s'] for r in reg['rules'].values() if isinstance(r.get('check'), dict)
           and r['check'].get('probe') == probe and r['check'].get('budget_s')]
    return max(got) if got else None


def _stage_rules(reg, stage, source):
    """Rules of one stage that have a judge, are not deferred and apply to a part of that source, sorted by id."""
    return [r for r in gate_registry.rules(reg, stage=stage, deferred=False)
            if isinstance(r.get('check'), dict) and r['check'].get('judge') and gate_registry.applies_to(r, source)]


def _question_probes(reg, source):
    """Fusion probes a standing question reads: the probes behind the fact tests (gate_attest.TEST_PROBES) that the
    "auto" of a design-stage attest rule names. Their facts let the gate answer a question itself where it cannot
    apply (gate_attest.fact_answer). Most are units a judge needs anyway; one that only a question reads is planned
    because of this. Such a unit that fails stops nothing: the question is asked."""
    import gate_attest
    out = set()
    if source != 'fusion' or gate_common.questions_off(gate_registry._project_root()):
        return out
    for r in gate_registry.rules(reg, stage='design', level='attest', deferred=False):
        a = r.get('attest') or {}
        if a.get('per') == 'part' and gate_registry.applies_to(r, source):
            for entry in a.get('auto') or []:
                for test in entry.get('when') or []:
                    out.update(gate_attest.TEST_PROBES.get(test) or ())
    return out


def _units(reg, part, entry, default_budget):
    source = entry.get('source', 'fusion')
    if source != 'fusion':
        return []
    probes = {r['check']['probe'] for r in _stage_rules(reg, 'design', source) if r['check'].get('needs') == 'fusion'}
    probes |= _question_probes(reg, source)
    probes.discard('fusion_scan')
    order = [p for p in PROBE_ORDER if p in probes] + sorted(probes - set(PROBE_ORDER))
    return [{'doc': entry['doc'], 'part': part, 'sig': entry.get('sig'), 'probe': p, 'state': 'todo', 'cursor': None, 'attempts': 0,
             'budget_s': min(float(_rule_budget(reg, p) or default_budget), gate_fusion_rpc.MAX_BUDGET_S), 'error': None} for p in order]


def units_for_part(reg, part, entry):
    """The Fusion units one part needs at the design stage: one per probe that a rule with a judge names (stage design,
    needs fusion, the rule applies to the part's source), and one per probe a standing question reads
    (_question_probes), in PROBE_ORDER. fusion_scan is not a part unit.

    reg    gate_registry.load()      part   part id      entry  the part's census entry
    Returns [{"doc", "part", "sig", "probe", "state": "todo", "cursor": None, "attempts": 0, "budget_s", "error": None}]
    without "id" and "out" (new_plan sets them). A generated part gets no Fusion unit: []."""
    return _units(reg, part, entry, gate_common.LAYOUT_DEFAULTS['fusion']['budget_s'])


def stale_units(root, reg, resolved=None, census=None, parts=None, target_key=None, layout=None, project=None, shadow=None):
    """Which units of which parts must be measured again: {part id: {probe: why (text) or None (its stored facts
    hold)}} for every live Fusion part of the census (or of `parts`), one entry per unit units_for_part() gives it.
    A unit is stale when gate_deps.why_stale() says so for the job the host would send now: the facts at the part's
    signature are missing or an error, nothing was recorded of what it read, the probe text, a job key, a parameter or
    a table it read changed, the part moved, or a body in a region it read changed, moved, came or went.

    resolved   the parameters of the run (gate_params.resolve for its target); None = for each unit the parameters of
               the target it was measured for (its deps "for"), as `gate.py status` sees it
    A unit that reads the census (its record names the job key "features", or its probe is one of NEEDS_CENSUS) whose
    own record holds is still reported as holding when the census unit of its part is stale: the plan runs the census
    first and looks again (new_plan: "recheck").

    Which record of the bodies a unit read decides is gate/project.json "deps" (gate_deps.deps_mode: "wide" by default).
    shadow  a dict to fill, or None: with "wide", {part id: {probe: why}} of every unit the wide record makes stale and
            the tight record would keep (gate_deps.py: the shadow, compared after the unit was measured again)"""
    census = census if census is not None else load_census(root)
    layout = layout if layout is not None else load_layout(root)
    project = project if project is not None else load_project(root)
    mode = gate_deps.deps_mode(project)
    by_target = {}

    def vals_for(key):
        if resolved is not None:
            return gate_params.values(resolved), gate_common.target_of(project, target_key)
        if key not in by_target:
            import gate_stamp
            try:
                tgt = gate_common.target_of(project, key)
                by_target[key] = (gate_params.values(gate_stamp._resolved_for(root, reg, tgt)), tgt)     # read once per process
            except (GateError, KeyError):
                by_target[key] = None
        return by_target[key]
    worlds, out = {}, {}
    for pid in sorted(parts if parts is not None else census['parts']):
        e = census['parts'].get(pid)
        if not e or e.get('gone') or e.get('source', 'fusion') != 'fusion' or not e.get('sig'):
            continue
        units = units_for_part(reg, pid, e)
        data = load_facts(root, pid, e['sig'])
        probes = data.get('probes') or {}
        cf = _probe_facts(data, 'fusion_census')
        features = {'holes': cf.get('holes') or [], 'openings': cf.get('openings') or []} if cf is not None else None
        res = {}
        for u in units:
            stored = probes.get(u['probe'])
            key = ((stored or {}).get('deps') or {}).get('for') if isinstance(stored, dict) else None
            got = vals_for(key)
            if got is None:
                res[u['probe']] = 'the parameters of its target cannot be read'
                continue
            vals, tgt = got
            try:
                job = _job(root, reg, vals, tgt, layout, project, census, 'check', dict(u, id='u-0000', out='gate/run/check/u-0000.json'), features=features)
            except GateError as err:
                res[u['probe']] = str(err)
                continue
            doc = census['docs'].get(e.get('doc')) or {}
            why = gate_deps.why_stale(stored, e, job, doc.get('world'), u['probe'], worlds, fusion=doc.get('fusion'),
                                      whole_tables=reg.get('tables') or {}, mode=mode)
            res[u['probe']] = why
            if why is not None and mode == 'wide' and shadow is not None and isinstance(((stored or {}).get('deps') or {}).get('bodies_tight'), dict):
                if gate_deps.why_stale(stored, e, job, doc.get('world'), u['probe'], worlds, fusion=doc.get('fusion'),
                                       whole_tables=reg.get('tables') or {}, mode='tight') is None:
                    shadow.setdefault(pid, {})[u['probe']] = why
        out[pid] = res
    return out


def _stored_units(root, part, entry):
    """{probe: stored entry} of the part at its present signature, else at the newest earlier signature whose facts
    are on disk (only for the expected cost of a unit: its facts are never taken from another geometry)."""
    sig = entry.get('sig')
    probes = (load_facts(root, part, sig).get('probes') or {}) if sig else {}
    if probes:
        return probes
    for old in reversed([h for h in entry.get('history') or [] if isinstance(h, str)] + ([entry['prev_sig']] if isinstance(entry.get('prev_sig'), str) else [])):
        got = load_facts(root, part, old).get('probes') or {}
        if got:
            return got
    return {}


def _expected_cost(stored):
    """Seconds of Fusion work the last measurement of a unit took ("work_s", the sum over its calls; an entry stored
    before work_s was kept: its last call's "elapsed_s"), or None when unknown."""
    if not isinstance(stored, dict):
        return None
    for k in ('work_s', 'elapsed_s'):
        v = stored.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            return float(v)
    return None


def _reads_census(probe, stored):
    """Does a unit read the census of its part (JOB["features"])? By its own record when it has one, else by its probe."""
    job = ((stored or {}).get('deps') or {}).get('job') if isinstance(stored, dict) else None
    return probe in NEEDS_CENSUS or (isinstance(job, dict) and 'features' in job)


def _number(root, plan):
    for i, u in enumerate(plan['units']):
        u['id'] = 'u-%04d' % (i + 1)
        u['out'] = gate_common.rel(root, os.path.join(gate_common.run_dir(root, plan['run']), 'units', u['id'] + '.json'))
    return plan


def new_plan(root, reg, parts, command='run', intent=None, audit=False, stale=None, shadow=None, only=None):
    """Make and save a plan for these parts (units_for_part each), name it in gate/run/current.json and return it.

    stale  stale_units() for these parts, or None (every unit is measured). A unit whose stored facts hold (None) is
           "done" with "reused": true and costs no call; a stale one is "todo" with "why". When the census unit of a
           part is stale, its units that read the census and hold otherwise are "todo" with "recheck": true: once the
           census is measured again they are looked at once more and need no call when its holes and openings came out
           the same (run_units).
    shadow stale_units(shadow=...): a stale unit that only the wide record measures gets "shadow": the wide reason.
           When it is done, its new facts are compared with those it had (run_units: gate/run/<run>/deps_shadow.jsonl)"""
    census, budget = load_census(root), gate_fusion_rpc.budget(load_layout(root))
    unknown = [p for p in parts if p not in census['parts']]
    if unknown:
        raise GateError('not in gate/census.json: %s (run gate.py scan; parts are discovered, not typed)' % ', '.join(unknown))
    plan = {'run': gate_common.new_run_id(), 'command': command, 'created': gate_common.now(), 'audit': bool(audit),
            'intent': list(intent) if intent is not None else None, 'parts': list(parts), 'units': [],
            'unreachable': [], 'changed_under_run': []}
    for p in parts:
        units = _units(reg, p, census['parts'][p], budget)
        if only:                                        # a partial run: only these probes are measured (census_again below sees the filtered list)
            units = [u for u in units if u['probe'] in only]
        stored = _stored_units(root, p, census['parts'][p])
        for u in units:
            cost = _expected_cost(stored.get(u['probe']))
            if cost is not None:
                u['expect_s'] = cost                    # what its last measurement took: a short unit may start in what is left of a call
        if stale is not None:
            mine = stale.get(p) or {}
            census_again = any(u['probe'] == 'fusion_census' and mine.get('fusion_census', 'not looked at') is not None for u in units)
            for u in units:
                why = mine.get(u['probe'], 'not looked at')
                if why is not None:
                    u['why'] = why
                    if (shadow or {}).get(p, {}).get(u['probe']):
                        u['shadow'] = shadow[p][u['probe']]
                elif census_again and _reads_census(u['probe'], stored.get(u['probe'])):
                    u['recheck'] = True
                else:
                    u['state'], u['reused'] = 'done', True
        plan['units'] += units
    _number(root, plan)
    save_plan(root, plan)
    write_json(gate_path(root, 'run', 'current.json'), {'run': plan['run']})
    return plan


# ---- running units -------------------------------------------------------------------------------------------------------

def _abs(root, relative):
    return os.path.join(os.path.abspath(root), *relative.split('/'))


def _partial_path(root, unit):
    return _abs(root, unit['out'])[:-len('.json')] + '.partial.json'


def _not_open(env, job):
    text = env.get('error') or ''
    if 'is not open' in text:
        return True
    docs = env.get('open_documents') or []
    return bool(docs) and job['doc'] not in docs and env.get('doc_version') is None and 'document' in text


def _first_line(text, n=300):
    return (str(text).strip().splitlines() or [''])[0][:n]


def _skip_doc(plan, doc_key, why=NOT_OPEN):
    if doc_key not in plan.setdefault('unreachable', []):
        plan['unreachable'].append(doc_key)
    for u in plan['units']:
        if u['doc'] == doc_key and not _finished(u):
            u['state'], u['error'] = 'skipped', why


def _note_over(u, env):
    """Keep a line in the plan when this call worked longer than the unit's budget. Returns the row or None."""
    worked, budget = env.get('elapsed_s'), u.get('budget_s')
    if not isinstance(worked, (int, float)) or isinstance(worked, bool) or not isinstance(budget, (int, float)) or worked <= budget:
        return None
    row = {'call': u.get('attempts', 0), 'elapsed_s': round(float(worked), 2), 'budget_s': budget, 'over_s': round(float(worked) - budget, 2),
           'late': bool(env.get('late'))}
    u['over'] = (u.get('over') or [])[-19:] + [row]
    return row


def _note_silent(u, why=None):
    """Keep a line in the plan for a call that got no answer within its time and left no result that could be used:
    it is said in the report even when the next call of the unit answers. Returns the row."""
    row = {'call': u.get('attempts', 0), 'budget_s': u.get('budget_s'), 'no_answer': True, 'late': False,
           'waited_s': (u.get('budget_s') or 0) + gate_fusion_rpc.HTTP_EXTRA_S, 'why': why or gate_fusion_rpc.NO_FILE}
    u['over'] = (u.get('over') or [])[-19:] + [row]
    return row


def _left(why):
    """'left no result', with the reason when there was a file that could not be used."""
    return 'left no result' if not why or why == gate_fusion_rpc.NO_FILE else 'left no result that can be used (%s)' % why


def over_note(row):
    """The note of one over-budget call, as the facts file of its unit holds it."""
    if row.get('no_answer'):
        return ('over budget: call %s of this unit got no answer within %g s (its budget is %g s) and %s'
                % (row.get('call'), row.get('waited_s') or 0, row.get('budget_s') or 0, _left(row.get('why'))))
    return ('over budget: call %s of this unit worked %g s, its budget is %g s (%g s over)%s'
            % (row.get('call'), row.get('elapsed_s'), row.get('budget_s'), row.get('over_s'),
               '; the answer came after the host had stopped waiting and was read from the result file by the next call' if row.get('late') else ''))


def over_budget(plan):
    """The units of a plan that ran over their budget, one row per call that did, and one per unit that was given up:
    [{"unit", "probe", "part" (the document key for a scan unit), "elapsed_s", "budget_s", "over_s", "late", "call"}],
    [{"unit", "probe", "part", "call", "budget_s", "no_answer": True, "waited_s", "why", "late": False}] for a call
    that got no answer and left no result, and [{"unit", "probe", "part", "budget_s", "not_run": True, "timeouts"}].
    [] for no plan."""
    rows = []
    for u in (plan or {}).get('units') or []:
        who = {'unit': u.get('id'), 'probe': u.get('probe'), 'part': u.get('part') or u.get('doc')}
        for o in u.get('over') or []:
            if isinstance(o, dict):
                rows.append(dict(who, **o))
        if u.get('over_budget') == 'not run':
            rows.append(dict(who, budget_s=u.get('budget_s'), not_run=True, timeouts=u.get('timeouts')))
    return rows


def over_lines(rows):
    """One line of text per row of over_budget()."""
    out = []
    for r in rows:
        head = 'OVER BUDGET  %s %s on %s' % (r.get('unit'), r.get('probe'), r.get('part'))
        if r.get('not_run'):
            out.append('%s: NOT RUN. %s calls in a row got no answer within its time (budget %g s) and left no result; its rules are NOT_RUN. '
                       'A defect of the probe: report it' % (head, r.get('timeouts'), r.get('budget_s') or 0))
        elif r.get('no_answer'):
            out.append('%s: call %s got no answer within %g s (budget %g s) and %s. A defect of the probe: report it'
                       % (head, r.get('call'), r.get('waited_s') or 0, r.get('budget_s') or 0, _left(r.get('why'))))
        else:
            out.append('%s: call %s worked %g s of %g s (%g s over)%s. A defect of the probe: report it'
                       % (head, r.get('call'), r.get('elapsed_s'), r.get('budget_s'), r.get('over_s'),
                          ', its answer was read late from the result file' if r.get('late') else ''))
    return out


def unit_error_lines(plan):
    """One line per unit of a plan that ended in an error of its probe, with the first line of that error (a unit that
    was given up over its budget has its OVER BUDGET line instead)."""
    return ['UNIT ERROR  %s %s on %s: %s' % (u.get('id'), u.get('probe'), u.get('part') or u.get('doc'), _first_line(u.get('error')))
            for u in (plan or {}).get('units') or [] if u.get('state') == 'error' and u.get('error') and u.get('over_budget') != 'not run']


def scan_over_budget(root):
    """over_budget() of the scan plan gate/run/scan.json names: the scan units are in no run plan, and a scan over its
    budget is a defect of fusion_scan like any other."""
    run = (read_json(gate_path(root, 'run', 'scan.json'), None) or {}).get('run')
    plan = load_plan(root, run) if run else None
    return over_budget(plan) if plan and plan.get('command') == 'scan' else []


def waiting_line(root):
    """One line about the call that was sent and has not answered yet (gate/run/pending.json), or None."""
    w = gate_fusion_rpc.waiting_call(path=gate_path(root, 'run', gate_fusion_rpc.PENDING))
    if w is None:
        return None
    return ('unit %s (%s on %s) was sent and its answer was not read yet (budget %g s): the next `gate.py run` reads its result file when there is one, '
            'and sends nothing while Fusion is still busy with it' % (w.get('id'), w.get('probe'), w.get('part') or w.get('doc'), w.get('deadline_s') or 0))


def _with_notes(u, env):
    """The envelope with one note per over-budget call of the unit (the facts file keeps the last envelope only)."""
    notes = [n for n in (env.get('notes') if isinstance(env.get('notes'), list) else []) if not str(n).startswith('over budget: ')]
    return dict(env, notes=notes + [over_note(o) for o in u.get('over') or [] if isinstance(o, dict)])


def _batchable(u):
    """May this unit share one Fusion call with others of its part: a part unit not begun (todo, no cursor, nothing in
    flight, no recheck), not the census (the units after it read what it finds), whose last measurement is known and
    took at most BATCH_UNIT_S."""
    cost = u.get('expect_s')
    return (bool(u.get('part')) and u.get('state') == 'todo' and u.get('cursor') is None and not u.get('flight') and not u.get('recheck')
            and u.get('probe') not in ('fusion_census', 'fusion_scan', 'fusion_export') and isinstance(cost, (int, float))
            and not isinstance(cost, bool) and 0 <= cost <= BATCH_UNIT_S)


def _batch_for(plan, u, deadline, taken):
    """The units that go into one call with `u`: those that follow it in the plan, of its part, _batchable, as many as
    fit into `deadline` (PACK_FACTOR over the sum of their last measurements, plus PACK_MARGIN_S), at most BATCH_MAX in
    all. [] when u goes alone. taken: ids of units an earlier batch already answered."""
    if not _batchable(u):
        return []
    units = plan['units']
    i = next(k for k, v in enumerate(units) if v is u)
    out, cost = [], float(u['expect_s'])
    for v in units[i + 1:]:
        if v.get('part') != u['part'] or len(out) + 1 >= BATCH_MAX:
            break
        if _finished(v) or v['id'] in taken:
            continue
        if not _batchable(v) or PACK_FACTOR * (cost + float(v['expect_s'])) + PACK_MARGIN_S > deadline:
            break                                           # the order of the units is kept: the next one waits for its own call
        out.append(v)
        cost += float(v['expect_s'])
    return out


def _loop(root, plan, wall_s, transport, pinger, job_for, on_done, on_fail, settle=None, batch=True):
    """Run the unfinished units of a plan in order. See run_units() for the rules. Returns True when units are left.
    settle(u): called before a unit that is not finished is sent; True = it needs no call (its stored facts hold).
    batch: short units of one part share one call (_batch_for, gate_fusion_rpc.run_batch). Each answer of such a call
    is processed when the loop reaches its unit, exactly as if its own call had brought it, with the deadline of the
    call as a unit packed into the rest of a call has it (no stall when it comes back unfinished; a look it cut short
    is measured again in a call of its own)."""
    url = gate_fusion_rpc.endpoint(load_layout(root))
    t0 = _clock()
    prefetched = {}                                         # unit id -> (envelope, deadline) of an answer a batched call brought
    acquire_lock(root)
    try:
        for u in plan['units']:
            def in_flight(job, u=u):                                # the call is about to go out: the unit notes which one
                u['flight'] = {'sent': job['sent'], 'cursor': gate_fusion_rpc.cursor_key(job['cursor'])}
                save_plan(root, plan)
            while not _finished(u):
                got = prefetched.pop(u['id'], None)
                if got is None and settle is not None and u['state'] == 'todo' and not u.get('flight') and settle(u):
                    save_plan(root, plan)
                    break
                if u['doc'] in plan.get('unreachable', []):
                    _skip_doc(plan, u['doc'])
                    break
                left = wall_s - (_clock() - t0)
                packed = None
                if got is not None:                                 # its answer came with an earlier unit's call
                    env, packed = got
                    job = job_for(u)
                    job['deadline_s'] = packed
                    u.pop('flight', None)
                    u['attempts'] += 1
                else:
                    if left + WALL_GRACE_S < u['budget_s']:
                        packed = _pack_deadline(u, left)
                        if packed is None:
                            save_plan(root, plan)
                            return True                             # less than its budget is left, and it is not known to be short: do not start it
                    job = job_for(u)
                    if packed is not None:
                        # a short unit in the rest of the call: it gets what is left as its deadline. A probe stops by its
                        # deadline and goes on in the next call (cursor) like any unit; a look it cut short is measured again
                        job['deadline_s'] = packed
                    read_late = False                               # the answer of this turn was read from a result file, nothing was sent
                    try:
                        # a call that was sent earlier and never answered: its result file is the answer (nothing is sent),
                        # or Fusion is still busy with it (FusionDown: nothing is sent), or it is over and left nothing
                        flight = u.get('flight')
                        state, env, why = gate_fusion_rpc.settle(job, url, pinger, flight)
                        if state in ('late', 'lost'):
                            u.pop('flight', None)
                            counted = not isinstance(flight, dict) or flight.get('counted')
                            if not counted:                         # the run ended while that call was out: it was never counted
                                u['attempts'] += 1
                        if state == 'late':
                            read_late = True
                        elif state == 'lost' and why and NEVER_RAN in why:
                            # the unit waited behind something else in Fusion and started after the host had stopped
                            # waiting: it measured nothing and says so. That is no call the unit ran over: not counted
                            if counted:
                                u['timeouts'] = max(0, u.get('timeouts', 0) - 1)
                        elif state == 'lost' and env is not None:   # it did answer, late, with an error: that error is its answer
                            read_late = True
                        elif state == 'lost':                       # over, and nothing that can be used: said, also when the next call answers
                            if not counted:
                                u['timeouts'] = u.get('timeouts', 0) + 1
                            u['lost'] = why
                            _note_silent(u, why)
                        if not read_late:
                            if u.get('timeouts', 0) >= MAX_TIMEOUTS:    # twice in a row no answer and no result: given up, never silently
                                u['state'], u['over_budget'] = 'error', 'not run'
                                u['error'] = ('%s: %d calls in a row got no answer within %g s and %s (budget %g s). A defect of the probe %s: report it'
                                              % (OVER_BUDGET, u['timeouts'], u['budget_s'] + gate_fusion_rpc.HTTP_EXTRA_S, _left(u.get('lost')), u['budget_s'], u['probe']))
                                on_fail(u, _with_notes(u, gate_fusion_rpc._made_envelope(job, u['error'])))
                                save_plan(root, plan)
                                continue
                            mates = _batch_for(plan, u, float(job['deadline_s']), prefetched) if batch else []
                            try:
                                if mates:
                                    jobs = [job] + [job_for(v) for v in mates]
                                    for j in jobs[1:]:
                                        j['deadline_s'] = job['deadline_s']

                                    def all_in_flight(sent_jobs, group=[u] + mates):
                                        for v, j in zip(group, sent_jobs):
                                            v['flight'] = {'sent': j['sent'], 'cursor': gate_fusion_rpc.cursor_key(j['cursor'])}
                                        save_plan(root, plan)
                                    envs = gate_fusion_rpc.run_batch(jobs, url, transport, pinger, before_send=all_in_flight)
                                    env, packed = envs[0], float(job['deadline_s'])
                                    for v, e in zip(mates, envs[1:]):
                                        prefetched[v['id']] = (e, packed)
                                else:
                                    env = gate_fusion_rpc.run_unit(job, url, transport, pinger, before_send=in_flight, settled=True)
                            except gate_fusion_rpc.FusionDown as e:
                                if e.timed_out:                     # this call was sent and got no answer: the unit keeps its note of it
                                    u['attempts'] += 1
                                    u['timeouts'] = u.get('timeouts', 0) + 1
                                    if isinstance(u.get('flight'), dict):
                                        u['flight']['counted'] = True
                                else:
                                    for v in [u] + mates:
                                        v.pop('flight', None)       # nothing reached Fusion
                                raise
                            u.pop('flight', None)
                            u['attempts'] += 1
                    except gate_fusion_rpc.FusionDown as e:
                        u['error'] = _first_line(e)
                        save_plan(root, plan)                       # the plan is saved first; nothing else is sent
                        raise
                _beat(root)
                _note_over(u, env)
                if isinstance(env.get('elapsed_s'), (int, float)) and not isinstance(env.get('elapsed_s'), bool):
                    u['work_s'] = round((u.get('work_s') or 0.0) + float(env['elapsed_s']), 3)     # the Fusion work of the unit over its calls
                if env.get('timeout'):                              # no answer in time, Fusion answers again, and no result file
                    u['timeouts'] = u.get('timeouts', 0) + 1
                    u['error'] = 'timeout'
                    u['lost'] = None
                    _note_silent(u)
                    save_plan(root, plan)
                    continue
                u['timeouts'] = 0
                u.pop('lost', None)
                if env.get('error'):
                    if _not_open(env, job):
                        _skip_doc(plan, u['doc'])
                    else:
                        u['error'] = _first_line(env['error'])
                        u['failures'] = u.get('failures', 0) + 1
                        if u['failures'] >= MAX_ATTEMPTS:
                            u['state'] = 'error'
                            on_fail(u, _with_notes(u, env))
                    save_plan(root, plan)
                    continue
                if u.get('part') and u.get('sig') and env.get('sig') and env['sig'] != u['sig']:
                    if u['part'] not in plan.setdefault('changed_under_run', []):
                        plan['changed_under_run'].append(u['part'])
                    for other in plan['units']:
                        if other.get('part') == u['part'] and not _finished(other):
                            other['state'], other['error'] = 'skipped', 'the part changed under the run: scan again'
                            prefetched.pop(other['id'], None)
                    save_plan(root, plan)
                    continue
                partial = read_json(_partial_path(root, u), None) if u['state'] == 'partial' else None
                if u.get('part'):
                    u['read'] = gate_deps.join_reads(u.get('read'), env)    # what the calls of the unit read: its dependencies
                got = dict(env.get('facts') or {})
                signing = got.pop('signing', None)                  # "the body is still being signed": progress, never a fact of the part
                merged = gate_fusion_rpc.merge_facts(partial, got)
                if not env.get('done'):
                    if isinstance(signing, dict):                   # such a call moves on when more faces are in than the call before reported
                        n = signing.get('faces_done')
                        n = n if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
                        stalled = n <= (u.get('signed') or 0)
                        u['signed'] = max(n, u.get('signed') or 0)
                    else:
                        stalled = env.get('cursor') == u['cursor'] and not got
                    u['failures'] = u.get('failures', 0) + 1 if stalled and packed is None else 0     # a call with less than its budget is no stall
                    u['state'], u['cursor'] = 'partial', env.get('cursor')
                    write_json(_partial_path(root, u), merged)
                    if u['failures'] >= MAX_ATTEMPTS:
                        u['state'], u['error'] = 'error', ('no progress: the signature of the body did not move on (%s of %s faces)' % (signing.get('faces_done'), signing.get('faces'))
                                                           if isinstance(signing, dict) else 'no progress: the probe returned the same cursor and no facts')
                        on_fail(u, _with_notes(u, dict(env, error=u['error'])))
                elif packed is not None and gate_deps.incomplete(merged):
                    # it finished in the rest of a call, but cut a look short for time: measured again with its whole budget
                    u['state'], u['cursor'], u['error'], u['expect_s'] = 'todo', None, None, None
                    u.pop('read', None)
                    if os.path.exists(_partial_path(root, u)):
                        os.remove(_partial_path(root, u))
                else:
                    u['state'], u['cursor'], u['error'], u['failures'] = 'done', None, None, 0
                    on_done(u, _with_notes(u, dict(env, facts=merged, done=True, cursor=None, work_s=u.get('work_s'), calls=u.get('attempts'))))
                    u.pop('read', None)
                    if os.path.exists(_partial_path(root, u)):
                        os.remove(_partial_path(root, u))
                save_plan(root, plan)
        save_plan(root, plan)
        return False
    finally:
        release_lock(root)


def _pack_deadline(u, left):
    """The deadline a unit gets when it starts in the rest of a call (`left` seconds), or None when it waits for the
    next call: only a unit not begun yet (todo, no cursor, nothing in flight) whose expected cost is known
    (u["expect_s"], new_plan) and fits: PACK_FACTOR * cost + PACK_MARGIN_S <= left."""
    cost = u.get('expect_s')
    if u.get('state') != 'todo' or u.get('cursor') is not None or u.get('flight') or not isinstance(cost, (int, float)) or isinstance(cost, bool):
        return None
    if left < PACK_MIN_S or PACK_FACTOR * float(cost) + PACK_MARGIN_S > left:
        return None
    return round(max(1.0, left - PACK_MARGIN_S / 2.0), 2)


def _probe_facts(data, probe):
    """The usable facts of one probe in a facts file: None when the unit never finished or ended in an error."""
    entry = (data.get('probes') or {}).get(probe)
    if not isinstance(entry, dict) or entry.get('error') or not entry.get('done', True) or not isinstance(entry.get('facts'), dict):
        return None
    return entry['facts']


def given_up(root, part, sig):
    """The probes of this part whose unit was given up over its budget at this signature (their stored entry holds an
    error that begins with OVER_BUDGET), sorted. Such a part was not judged in full: an audit does not inherit it."""
    probes = (load_facts(root, part, sig).get('probes') or {}) if sig else {}
    return sorted(k for k, e in probes.items() if isinstance(e, dict) and str(e.get('error') or '').startswith(OVER_BUDGET))


def _features(root, part, sig):
    facts = _probe_facts(load_facts(root, part, sig), 'fusion_census') if sig else None
    if facts is None:
        return None
    return {'holes': facts.get('holes') or [], 'openings': facts.get('openings') or []}


def _unit_args(probe, vals):
    if probe == 'fusion_thin':                 # the probe lists faces up to this width; the judge holds the limit
        return {'width_cap': max(2.0, round(1.5 * float(vals.get('walls.min_face_width') or 0.0), 2))}
    return {}


_LOAD = object()
_PROBE_TABLES = {}


def _probe_tables(reg):
    """gate_deps.probe_tables() of the registry's tables, made once per registry: what a unit is sent."""
    key = (id(reg.get('tables')), reg.get('sha256'))
    if key not in _PROBE_TABLES:
        _PROBE_TABLES.clear()
        _PROBE_TABLES[key] = gate_deps.probe_tables(reg.get('tables') or {})
    return _PROBE_TABLES[key]


def _job(root, reg, vals, target, layout, project, census, run, u, features=_LOAD):
    """The job of one unit as it is sent (gate_fusion_rpc.new_job), from the census, the layout and the parameters.
    features: the census features of the part when the caller has them (else they are read from its facts file)."""
    doc, entry = gate_common.document_entry(layout, u['doc']), census['parts'].get(u['part']) or {}
    if u['probe'] == 'fusion_census':
        features = None
    elif features is _LOAD:
        features = _features(root, u['part'], u['sig'])
    return gate_fusion_rpc.new_job(
        u['id'], run, u['probe'], doc.get('name'), u['doc'], _abs(root, u['out']), part=u['part'],
        component_id=entry.get('component_id'), occurrence=entry.get('occurrence'), body=entry.get('body'), sig=u['sig'],
        cursor=u.get('cursor'), deadline_s=u.get('budget_s') or 20, params=vals, tables=_probe_tables(reg), target=target, features=features,
        not_printed=project['not_printed'], motion_groups=doc.get('motion_groups'), args=_unit_args(u['probe'], vals))


def run_units(root, reg, resolved, plan, wall_s=20.0, transport=None, pinger=None, target_key=None):
    """Run the units of a plan that are not finished, in order, until none is left or the wall time is used up.

    For each unit: build the job (gate_fusion_rpc.new_job with params = gate_params.values(resolved), the tables, the
    census features of the part once fusion_census is done), run it (gate_fusion_rpc.run_unit), then
      - before anything is sent: a call that was sent earlier and never answered (gate_fusion_rpc.settle, with the
        unit's own note of that call, "flight"). Its result file, when it is the answer of this very call (same run,
        unit, signature and cursor), is taken as the answer, marked late, whatever other gate command ran in between;
        a result file that holds an error is that call's answer too: the error is counted with its own text;
        while Fusion is still busy with a call, FusionDown is raised and nothing is sent;
      - no answer in time and no result, MAX_TIMEOUTS calls in a row: the unit is "error" with a text that begins
        with OVER_BUDGET, its rules are NOT_RUN with that text, and the run goes on with the next unit. A unit is
        given up only after its result file was looked for;
      - a call that worked longer than the unit's budget, and a call that got no answer and left no result: one row
        in the unit's "over" (over_budget() lists them), also when a later call of the unit answers;
      - envelope "error": the unit is "error" after MAX_ATTEMPTS, its text is kept in the plan;
      - envelope "sig" differs from the plan's: the part changed under the run; its units are "skipped" and the part is
        reported as changed (scan again);
      - "done" false: state "partial", the cursor is kept, the facts so far are merged (gate_fusion_rpc.merge_facts);
      - "done" true: the merged facts are stored with gate_common.save_probe_facts and the unit is "done".
    The plan is saved after every unit. A unit is not started when less than its budget_s is left of wall_s, unless
    its last measurement is known to be short (u["expect_s"], new_plan): then it starts with what is left as its
    deadline (_pack_deadline), so one call of 20 s carries several small units. Such a call that comes back unfinished
    is no stall; one whose facts say a look was cut short for time (gate_deps.incomplete) is measured again in a call
    of its own. Each finished unit stores "work_s" (its Fusion work over all its calls) and "calls" beside its facts.
    A document that is not open: its units are "skipped", the part is unreachable (old stamps stay valid, no new stamp).

    target_key: the .3mf project whose declared target the units are run for (None = the only declared target).

    Returns {"done": n, "total": m, "more": bool, "unreachable": [doc keys], "changed_under_run": [part ids]}.
    Raises gate_fusion_rpc.FusionDown when Fusion stops answering (the plan is saved first)."""
    root = os.path.abspath(root)
    layout, project, census = load_layout(root), load_project(root), load_census(root)
    vals, target = gate_params.values(resolved), gate_common.target_of(project, target_key)

    def job_for(u):
        return _job(root, reg, vals, target, layout, project, census, plan['run'], u)

    def on_done(u, env):
        entry = census['parts'].get(u['part']) or {}
        deps = gate_deps.record(u.get('read'), job_for(u), entry, (census['docs'].get(u['doc']) or {}).get('world'), u['probe'], target_key)
        if u.get('shadow'):
            _shadow_note(root, plan, u, env)
        gate_common.save_probe_facts(root, u['part'], u['sig'], dict(env, part=u['part'], sig=u['sig'], probe=u['probe'], deps=deps))

    def settle(u):
        # a unit planned only because the census of its part was measured again: when the holes and openings came out
        # the same (and nothing else it read changed), its stored facts hold and it needs no call
        if not u.get('recheck'):
            return False
        u.pop('recheck', None)
        data = load_facts(root, u['part'], u['sig'])
        doc = census['docs'].get(u['doc']) or {}
        why = gate_deps.why_stale((data.get('probes') or {}).get(u['probe']), census['parts'].get(u['part']) or {}, job_for(u),
                                  doc.get('world'), u['probe'], fusion=doc.get('fusion'), whole_tables=reg.get('tables') or {},
                                  mode=gate_deps.deps_mode(project))
        if why is not None:
            u['why'] = why
            return False
        u['state'], u['reused'], u['error'] = 'done', True, None
        return True

    def on_fail(u, env):
        # an error entry replaces older facts of this probe: a unit that failed leaves its rules NOT_RUN, not "as before"
        bad = dict(env, part=u['part'], sig=u['sig'], probe=u['probe'], facts={}, done=False, error=u['error'] or 'error')
        if u.get('over_budget') == 'not run':
            # given up: it stays given up (its NOT_RUN row) while nothing of the document, its inputs or its probe
            # changed, instead of costing two waits on every run (gate_deps.record_given_up; `run --fresh` tries it)
            bad['deps'] = gate_deps.record_given_up(u.get('read'), job_for(u), census['parts'].get(u['part']) or {},
                                                    (census['docs'].get(u['doc']) or {}).get('world'), u['probe'], target_key)
        gate_common.save_probe_facts(root, u['part'], u['sig'], bad)
        if u['probe'] == 'fusion_census':
            for other in plan['units']:
                if other['part'] == u['part'] and other['probe'] in NEEDS_CENSUS and not _finished(other):
                    other['state'], other['error'] = 'skipped', 'no census: fusion_census ended in an error'
                    gate_common.save_probe_facts(root, u['part'], u['sig'], dict(bad, probe=other['probe'], job=other['id'], error=other['error']))

    more = _loop(root, plan, wall_s, transport, pinger, job_for, on_done, on_fail, settle=settle, batch=(layout.get('fusion') or {}).get('batch', True) is not False)
    if plan.get('unreachable'):
        _mark_docs(root, plan['unreachable'], False)
    n, m = progress(plan)
    return {'done': n, 'total': m, 'more': more, 'unreachable': list(plan.get('unreachable') or []),
            'changed_under_run': list(plan.get('changed_under_run') or [])}


def _shadow_note(root, plan, u, env):
    """A unit of the shadow (new_plan "shadow") is done: its new facts against the facts it had at this signature, one
    line in gate/run/<run>/deps_shadow.jsonl (gate_deps.shadow_row). Its result in the plan unit: "shadow_same"."""
    old = (load_facts(root, u['part'], u['sig']).get('probes') or {}).get(u['probe'])
    if not isinstance(old, dict) or not isinstance(old.get('facts'), dict) or old.get('error'):
        return
    row = gate_deps.shadow_row(u['part'], u['probe'], u['shadow'], old['facts'], env.get('facts'))
    row['run'] = plan['run']
    path = os.path.join(gate_common.run_dir(root, plan['run']), gate_deps.SHADOW_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n')
    u['shadow_same'] = row['same']


def shadow_line(plan):
    """One line for the end of a run about its shadow units, or None when it had none."""
    rows = [u for u in (plan or {}).get('units') or [] if u.get('shadow') and 'shadow_same' in u]
    if not rows:
        return None
    bad = [u for u in rows if not u['shadow_same']]
    return ('deps shadow: %d unit(s) that the tight record would have kept were measured (the wide one decides): %d gave the same facts%s '
            '(gate/run/%s/%s)' % (len(rows), len(rows) - len(bad), '' if not bad else ', %d DIFFERED: %s. The tight record would have missed them: '
                                  'report it, and keep "deps": "wide"' % (len(bad), ', '.join('%s %s' % (u['part'], u['probe']) for u in bad[:4])),
                                  plan['run'], gate_deps.SHADOW_FILE))


def _mark_docs(root, doc_keys, is_open):
    """Note in the census which documents were found open or not open, when that differs from what it says."""
    census, changed = load_census(root), False
    for key in doc_keys:
        d = census['docs'].setdefault(key, {})
        if d.get('open') is not is_open:
            d['open'], changed = is_open, True
    if changed:
        write_json(gate_common.census_path(root), census)


# ---- scan: discovery -----------------------------------------------------------------------------------------------------

def _scan_plan(root, layout, doc_keys, census=None):
    pointer = gate_path(root, 'run', 'scan.json')
    run = (read_json(pointer, None) or {}).get('run')
    plan = load_plan(root, run) if run else None
    if plan and plan.get('command') == 'scan' and [u['doc'] for u in plan['units']] == doc_keys and progress(plan)[0] < len(doc_keys):
        return plan
    budget = gate_fusion_rpc.budget(layout)
    plan = {'run': gate_common.new_run_id(), 'command': 'scan', 'created': gate_common.now(), 'audit': False, 'intent': None, 'parts': [],
            'unreachable': [], 'changed_under_run': [],
            'units': [{'doc': k, 'part': None, 'sig': None, 'probe': 'fusion_scan', 'state': 'todo', 'cursor': None, 'attempts': 0,
                       'budget_s': budget, 'error': None} for k in doc_keys]}
    for u in plan['units']:                             # what its last scan took: a short scan may start in the rest of a call
        cost = ((census or {}).get('docs', {}).get(u['doc']) or {}).get('scan_s')
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            u['expect_s'] = float(cost)
    _number(root, plan)
    save_plan(root, plan)
    write_json(pointer, {'run': plan['run']})
    return plan


def _history(old):
    """The signatures a part had before, oldest first (the last 20): where the facts of its earlier geometries lie."""
    return ([s for s in old.get('history') or [] if s != old.get('sig')] + ([old['sig']] if old.get('sig') else []))[-20:]


def _merge_doc(root, census, doc, env, result, registered=None):
    """Lay the finished scan of one document over the census.

    registered  {document name: key} of every document of gate/layout.json. A printed body that comes from a LINKED
                document (the scan names it in "linked") is a part of this document, with "linked" in its census
                entry, unless that document is registered itself: then its parts are judged there and are left out
                here, so nothing is judged twice. Both cases are counted per linked document in
                census["docs"][key]["linked"] and result["linked"][key], and the scan prints one line for each.

    "stl_names": a part whose mapped file is another part's (the default file of a part of this document, or the STL
    of a live part of another document or of a part made by code) is a clash line. The part STAYS in the census with
    the "stl" it had (the default file for a new part): a wrong map never reads as "the part left the document".
    result["stl_moved"] gets (part id, old "stl", new "stl") for every known part whose file changed with this scan,
    result["stl_unmatched"] (document key, map key, file) for every key of the map that names no part of this scan."""
    key, now, parts = doc['key'], gate_common.now(), census['parts']
    facts, seen, linked = env.get('facts') or {}, {}, {}
    names = doc.get('stl_names') or {}
    for p in facts.get('parts') or []:
        other = p.get('linked') if isinstance(p.get('linked'), str) and p.get('linked') else None
        if other or p.get('external'):
            there = (registered or {}).get(other) if other else None
            row = linked.setdefault(other or '?', {'parts': 0, 'registered': there if there != key else None})
            row['parts'] += 1
            if row['registered']:
                continue
        pid = gate_common.part_id(key, p.get('name'))
        if pid in seen or (parts.get(pid) or {}).get('source') == 'generated' or not p.get('sig'):
            why = 'no signature' if not p.get('sig') else 'the id is taken by %r' % ((seen.get(pid) or parts.get(pid) or {}).get('name'),)
            result['clashes'].append('%s: body %r of document %r is left out (%s): give it its own name' % (pid, p.get('name'), doc.get('name'), why))
            continue
        seen[pid] = p
    kept = {}                                            # part id -> the "stl" a part keeps because its mapped file is another part's
    if names and doc.get('stl_dir'):
        plain = dict(doc, stl_names={})
        for pid, (want, other, default_only) in sorted(gate_common.stl_map_refusals(doc, {pid: seen[pid].get('name') for pid in seen}, parts).items()):
            before = (parts.get(pid) or {}).get('stl')
            kept[pid] = before if before and gate_common.stl_key(before) != gate_common.stl_key(want) else gate_common.stl_of(plain, seen[pid].get('name'))
            what = ('the default STL name of the part %s (a file named after one part is never given to another)' if default_only else 'the STL of the part %s') % other
            result['clashes'].append('%s: "stl_names" of document %r gives it the file %s, which is %s: the part keeps %s until the map is changed '
                                     '(gate/layout.json, then scan again)' % (pid, key, want.rsplit('/', 1)[1], what, kept[pid]))
        known = {seen[pid].get('name') for pid in seen} | {pid.split('/')[1] for pid in seen}
        result.setdefault('stl_unmatched', []).extend((key, k, names[k]) for k in sorted(names) if k not in known)
    for pid in sorted(seen):
        p, old = seen[pid], parts.get(pid)
        e = dict(old or {})
        e.update(doc=key, name=p.get('name'), source='fusion', component_id=p.get('component_id'), occurrence=p.get('occurrence'),
                 body=p.get('body'), pose=p.get('pose'), matrix=p.get('matrix'), bbox=p.get('bbox'), gone=False,
                 stl=kept[pid] if pid in kept else gate_common.stl_of(doc, p.get('name')))
        if old and old.get('stl') and e['stl'] and old['stl'] != e['stl']:
            result.setdefault('stl_moved', []).append((pid, old['stl'], e['stl']))
        for k in ('copies', 'volume', 'area', 'faces'):
            if k in p:
                e[k] = p[k]
        if 'shown' in p or 'visible' in p:
            e['shown'] = bool(p.get('shown') if 'shown' in p else p.get('visible'))
        e.pop('linked', None)
        if p.get('linked') or p.get('external'):
            e['linked'] = p.get('linked') or '?'
        if old is None:
            e.update(sig=p['sig'], prev_sig=None, first_seen=now, changed=now)
            result['new'].append(pid)
        elif old.get('sig') != p['sig']:
            e.update(sig=p['sig'], prev_sig=old.get('sig'), changed=now, history=_history(old))
            result['new' if old.get('gone') else 'changed'].append(pid)
        elif old.get('gone'):
            result['new'].append(pid)
        e.pop('gone_at', None)
        parts[pid] = e
    for pid in sorted(parts):
        e = parts[pid]
        if e.get('doc') == key and e.get('source', 'fusion') == 'fusion' and pid not in seen and not e.get('gone'):
            e['gone'], e['gone_at'] = True, now
            result['gone'].append(pid)
    census['docs'][key] = {'name': doc.get('name'), 'doc_version': env.get('doc_version'), 'open': True, 'scanned': now,
                           'parts': len(seen), 'refs': len(facts.get('refs') or [])}
    if linked:
        census['docs'][key]['linked'] = linked
    census['docs'][key]['world'] = gate_deps.world(facts)    # every body with its signature, pose and visibility: what units depend on
    if env.get('fusion_version'):
        census['docs'][key]['fusion'] = str(env['fusion_version'])     # facts measured in another version of Fusion are measured again
    refs = [r for r in facts.get('references') or [] if isinstance(r, dict)]
    if refs:
        census['docs'][key]['references'] = refs


def _merge_generators(root, census, layout, doc_key, result, only=None):
    """Parts made by code: every generator of gate/layout.json is a part whose signature is the geom_hash of its STL.
    only: look at this one part and leave the others as they are."""
    now, parts, listed = gate_common.now(), census['parts'], set()
    for g in layout.get('generators') or []:
        pid = g.get('part')
        key, _ = gate_common.split_part_id(pid)
        if (doc_key and key != doc_key) or (only and pid != only):
            continue
        old = parts.get(pid)
        if old and old.get('source', 'fusion') == 'fusion' and not old.get('gone'):
            result['clashes'].append('%s: the generator %r names a part that the document already has' % (pid, g.get('source')))
            continue
        listed.add(pid)
        path = os.path.join(root, *str(g.get('stl') or '').split('/'))
        sig = gate_common.sibling('meshid').stl_ids(path)['geom_hash'] if g.get('stl') and os.path.isfile(path) else None
        e = dict(old or {})
        e.update(doc=key, name=pid.split('/')[1], source='generated', generator=g.get('source'), stl=g.get('stl'), gone=False)
        if old is None or old.get('source') != 'generated':
            e.update(sig=sig, prev_sig=None, first_seen=now, changed=now)
            result['new'].append(pid)
        elif old.get('sig') != sig:
            e.update(sig=sig, prev_sig=old.get('sig'), changed=now, history=_history(old))
            result['new' if old.get('gone') else 'changed'].append(pid)
        elif old.get('gone'):
            result['new'].append(pid)
        e.pop('gone_at', None)
        parts[pid] = e
    for pid in sorted(parts):
        e = parts[pid]
        if e.get('source') == 'generated' and pid not in listed and not e.get('gone') and (not doc_key or e.get('doc') == doc_key) and not only:
            e['gone'], e['gone_at'] = True, now
            result['gone'].append(pid)


PULSE_MIN_S = 3.0               # seconds a pulse call needs at least of what is left of the wall
FOLD_MIN_S = PULSE_MIN_S + 2.0  # what must be left of a call for the confirming scan to be made in it (cmd_run)


def _pulse_inputs(layout, project, doc):
    """16 hex over what a census entry is made from besides the model: the code of the scan and of the pulse, the
    not-printed names, the document's entry of gate/layout.json and the names of every registered document. A pulse
    taken under other inputs never stands for a scan."""
    return gate_common.digest([gate_deps.code('fusion_scan'), gate_deps.code('fusion_pulse'), project.get('not_printed') or [], doc,
                               sorted(d.get('name') or '' for d in layout.get('documents') or [])])


def pulse(root, layout, project, doc, wall_s, transport=None, pinger=None):
    """(16 hex or None, fusion version or None, why None): the pulse of one document now (probes/fusion_pulse.py), one
    short Fusion call under the run lock. None (scan in full) when the call fails, times out or the probe cannot vouch."""
    url = gate_fusion_rpc.endpoint(layout)
    out = gate_path(root, 'run', 'pulse', 'units', '%s.json' % doc['key'])     # where a unit's result lies: gate/run/<run>/units
    job = gate_fusion_rpc.new_job('u-pulse-%s' % doc['key'], 'pulse', 'fusion_pulse', doc['name'], doc['key'], out,
                                  deadline_s=max(2.0, min(gate_fusion_rpc.budget(layout), wall_s - 1.0)), not_printed=project['not_printed'])
    acquire_lock(root)
    try:
        env = gate_fusion_rpc.run_unit(job, url, transport, pinger)
    finally:
        release_lock(root)
    if env.get('error') or env.get('timeout'):
        return None, None, _first_line(env.get('error') or 'timeout', 160)
    facts = env.get('facts') or {}
    return facts.get('pulse'), env.get('fusion_version'), facts.get('why')


def scan(root, doc_key=None, wall_s=20.0, transport=None, pinger=None, use_pulse=False):
    """Discover parts. Runs fusion_scan on the registered documents (gate/layout.json), merges the result into
    gate/census.json and returns {"census": the census, "done": bool, "next": [doc keys not finished],
    "new": [part ids], "changed": [part ids], "gone": [part ids], "unreachable": [doc keys]}.

    A part's id is gate_common.part_id(doc key, name); its "stl" is gate_common.stl_of(document, name): the file the
    document's "stl_names" gives it, else <stl_dir>/<slug>.stl. A mapped file that is another part's is a clash: the
    part stays in the census with the file it had (_merge_doc). After the merge every file that two live parts name
    as their STL (gate_common.shared_stls: parts of every document and every generator) is one more clash line.
    A part whose signature differs gets "prev_sig", "changed" and
    "history" (its earlier signatures, oldest first: the facts of an earlier checked geometry are found through it);
    a part no longer found gets "gone": true (its stamps stay on disk). Generators of gate/layout.json become parts
    with "source": "generated" and "sig" = the geom_hash of their STL.
    Raises gate_fusion_rpc.FusionDown when Fusion does not answer.

    Also returned: "errors" {doc key: why its scan failed}, "clashes" (bodies left out because two give one part id,
    and STL files that two parts name), "progress" (n, m) of the scan units, "run", "stl_moved" [{"part", "from",
    "to", "left": the old file is still in the folder and no part names it}] for every part whose STL path changed
    with this scan, "stl_unmatched" [(doc key, map key, file)] for every key of "stl_names" that names no part, and
    "over_budget" (over_budget() of the scan plan: calls of a scan unit that worked longer than their budget).
    A document is merged into the census only when its scan is complete: half a list would mark the rest as gone.
    A finished scan also returns "notes": the lines of scan_notes() for the documents it read (linked documents, equal
    bodies that are probably bought, parts in no motion group), each said once per document.

    use_pulse  first one short call per document (pulse()): when its pulse, the Fusion version and the inputs
               (_pulse_inputs) are those of the last scan of that document, nothing a scan reads has changed and the
               document is not scanned again ("unchanged": [doc keys]). A scan stores the pulse its own first call read
               (fusion_scan), only when the whole scan was that one call: one script, one state of the model.
               `gate.py scan` and `run --fresh` scan in full."""
    root = os.path.abspath(root)
    layout, project = load_layout(root), load_project(root)
    if doc_key is not None:
        gate_common.document_entry(layout, doc_key)
    docs = [d for d in layout['documents'] if doc_key in (None, d['key'])]
    if not docs and not layout.get('generators'):
        raise GateError('gate/layout.json registers no document and no generator: there is nothing to discover')
    for d in docs:
        if not d.get('name'):
            raise GateError('gate/layout.json: document %r has no "name" (the name of the Fusion document)' % (d.get('key'),))
    result = {'new': [], 'changed': [], 'gone': [], 'unreachable': [], 'errors': {}, 'clashes': [], 'next': [], 'done': True,
              'stl_moved': [], 'stl_unmatched': []}
    census, by_key = load_census(root), {d['key']: d for d in docs}
    _merge_generators(root, census, layout, doc_key, result)        # parts made by code need no Fusion: they are known before the first call
    write_json(gate_common.census_path(root), census)
    result['unchanged'], pulses, t0 = [], {}, _clock()
    if use_pulse:
        for d in list(docs):
            left = wall_s - (_clock() - t0)
            was = (census['docs'].get(d['key']) or {}).get('pulse')
            if left < PULSE_MIN_S or not isinstance(was, dict):
                continue
            got, version, _why = pulse(root, layout, project, d, left, transport, pinger)
            if got is None:
                continue
            pulses[d['key']] = {'digest': got, 'fusion': version, 'inputs': _pulse_inputs(layout, project, d)}
            here = census['docs'].get(d['key']) or {}
            if pulses[d['key']] == was and here.get('open') is not False and here.get('world') and version == here.get('fusion', version):
                result['unchanged'].append(d['key'])
        docs = [d for d in docs if d['key'] not in result['unchanged']]
        wall_s -= _clock() - t0
    if docs:
        plan = _scan_plan(root, layout, [d['key'] for d in docs], census)

        def job_for(u):
            doc = by_key[u['doc']]
            return gate_fusion_rpc.new_job(u['id'], plan['run'], 'fusion_scan', doc['name'], doc['key'], _abs(root, u['out']), cursor=u['cursor'],
                                           deadline_s=u['budget_s'], not_printed=project['not_printed'], motion_groups=doc.get('motion_groups'))

        registered = {d.get('name'): d['key'] for d in layout['documents'] if d.get('name')}

        def on_done(u, env):
            _merge_doc(root, census, by_key[u['doc']], env, result, registered)
            if isinstance(env.get('work_s'), (int, float)) and not isinstance(env.get('work_s'), bool):
                census['docs'][u['doc']]['scan_s'] = round(float(env['work_s']), 2)     # what the next scan is expected to take
            got = (env.get('facts') or {}).get('pulse')
            if got and int(u.get('attempts') or 0) <= 1:
                # the scan's own pulse, read in the same call as the whole scan: one script, one state of the model. A
                # scan of several calls stores none (an edit between two calls would hide behind it): the next run scans
                census['docs'][u['doc']]['pulse'] = {'digest': got, 'fusion': env.get('fusion_version'), 'inputs': _pulse_inputs(layout, project, by_key[u['doc']])}
            census['scanned'] = gate_common.now()
            write_json(gate_common.census_path(root), census)

        def on_fail(u, env):
            result['errors'][u['doc']] = u['error'] or 'error'

        more = _loop(root, plan, wall_s, transport, pinger, job_for, on_done, on_fail)
        for u in plan['units']:
            if u['state'] == 'error':
                result['errors'][u['doc']] = u['error'] or 'error'
            elif u['state'] == 'skipped':
                result['unreachable'].append(u['doc'])
                census['docs'].setdefault(u['doc'], {'name': by_key[u['doc']].get('name')})['open'] = False
            elif not _finished(u):
                result['next'].append(u['doc'])
        result.update(done=not more, progress=progress(plan), run=plan['run'], over_budget=over_budget(plan))
    else:
        result.update(progress=(0, 0), run=None, over_budget=[])
        if result['unchanged']:
            result['done'] = True
    for path, pids in sorted(gate_common.shared_stls(census, layout).items()):      # two parts, one file: whatever the reason
        result['clashes'].append('%s: the parts %s name this one file as their STL, an export of one overwrites the other: give one of them another file '
                                 '("stl_names", "stl_dir" or the generator\'s "stl" in gate/layout.json), then scan again' % (path, ' and '.join(pids)))
    named = {gate_common.stl_key(e['stl']) for e in census['parts'].values() if isinstance(e, dict) and e.get('stl') and not e.get('gone')}
    result['stl_moved'] = [{'part': pid, 'from': old, 'to': new,
                            'left': gate_common.stl_key(old) not in named and os.path.isfile(os.path.join(root, *old.split('/')))}
                           for pid, old, new in result['stl_moved']]
    if result['done']:
        census['scanned'] = gate_common.now()
        result['notes'] = scan_notes(root, layout, census, [d['key'] for d in docs if d['key'] not in result['unreachable'] and d['key'] not in result['errors']])
    write_json(gate_common.census_path(root), census)
    result['census'] = census
    return result


def _own_number(root, name):
    """One parameter of this project by name, or None when the registry or the parameter cannot be read."""
    try:
        reg = gate_registry.load_default(root)
        return gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), None), [name]).get(name)
    except Exception:                                   # a hint must never stop a scan: no number, no hint
        return None


def same_bodies(parts, at_least):
    """Parts that are one of many equal bodies in one component: {(component id, volume, area, faces): [part ids]} for
    every such family of `at_least` bodies or more. Rollers, balls and pins come like that; printed parts rarely do.
    parts: {part id: census entry}. A hint for the reader, never a reason to leave a part out."""
    families = {}
    for pid, e in parts.items():
        if all(isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool) for k in ('volume', 'area', 'faces')) and e.get('component_id'):
            families.setdefault((e['component_id'], round(e['volume'], 1), round(e['area'], 1), e['faces']), []).append(pid)
    return {k: sorted(v) for k, v in families.items() if isinstance(at_least, (int, float)) and len(v) >= at_least}


def parts_in_no_group(parts, groups):
    """Part ids that match none of the declared motion groups, sorted. The test is the one of the gap probe: the first
    group one of whose patterns is found in the occurrence path of the part (its name when it sits at the root)."""
    import re
    from probes import _geom_rings

    def search(pattern, text):
        try:
            return bool(pattern) and re.search(pattern, text or '') is not None
        except re.error:
            return False
    return sorted(pid for pid, e in parts.items() if _geom_rings.group_of(e.get('occurrence') or e.get('name') or '', groups, search) is None)


def stale_references(census, key):
    """Lines for the linked documents that document `key` uses in an older version than the newest one (the census
    "references" of its last scan: DocumentReference.isOutOfDate, or a version under the newest): its parts from that
    document are measured as they were. [] when none is, or the scan did not say."""
    out = []
    for r in (census['docs'].get(key) or {}).get('references') or []:
        if not isinstance(r, dict):
            continue
        v, latest = r.get('version'), r.get('latest')
        old = r.get('out_of_date') is True or (isinstance(v, int) and isinstance(latest, int) and v < latest)
        if old:
            out.append('%s: it uses the linked document %r in version %s, %s: what it holds of that document is measured as '
                       'it was. Get the latest version in Fusion (the out-of-date reference), save, and scan again'
                       % (key, r.get('name') or '?', v if v is not None else '?',
                          'the newest is %s' % latest if isinstance(latest, int) else 'Fusion says it is out of date'))
    return out


def scan_notes(root, layout, census, doc_keys):
    """What a finished scan tells the reader ONCE per document, never once per part (lines of text):

      LINKED     how many printed bodies come from which linked document, and who judges them: this document, or the
                 linked document itself when it is registered in gate/layout.json (then they are not parts here);
      OUT OF DATE  a linked document this one uses in an older version than its newest (stale_references);
      HINT       families of equal bodies in one component (fusion.bought_hint_same_bodies): probably bought, the
                 owner adds a "not_printed" pattern or leaves them. Nothing is left out by this;
      NO GROUP   parts that match no motion group, or a document that declares none: clearance.min_gap_moving does
                 not measure them. The judge says the same per part as a NOT_RUN row."""
    out, n_same = [], _own_number(root, 'fusion.bought_hint_same_bodies')
    for key in doc_keys:
        doc = gate_common.document_entry(layout, key)
        mine = {pid: e for pid, e in census['parts'].items() if e.get('doc') == key and not e.get('gone') and e.get('source', 'fusion') == 'fusion'}
        for name, row in sorted(((census['docs'].get(key) or {}).get('linked') or {}).items()):
            what = 'a linked document whose name Fusion did not give' if name == '?' else 'the linked document %r' % name
            if row.get('registered'):
                out.append('LINKED   %s: %d printed bodies come from %s, registered as %r: they are judged there and are no parts of %s (open it and scan it)'
                           % (key, row.get('parts', 0), what, row['registered'], key))
            else:
                out.append('LINKED   %s: %d parts come from %s, which gate/layout.json does not register: they are judged here. '
                           'Its bought parts carry no prefix of this project: check the "not_printed" patterns' % (key, row.get('parts', 0), what))
        for line in stale_references(census, key):
            out.append('OUT OF DATE %s' % line)
        families = same_bodies(mine, n_same)
        if families:
            names = sorted({'%d x %r' % (len(v), (mine[v[0]].get('name') or v[0]).split(' / ')[0]) for v in families.values()})
            out.append('HINT     %s: %d parts are one of %g or more equal bodies in one component (rollers, balls and pins come like that). If they are bought, '
                       'add a "not_printed" pattern to gate/project.json: %s%s' % (key, sum(len(v) for v in families.values()), n_same, ', '.join(names[:6]),
                                                                                   ' and %d more' % (len(names) - 6) if len(names) > 6 else ''))
        groups = doc.get('motion_groups') or []
        if mine and not groups:
            out.append('NO GROUP %s: the document declares no "motion_groups" in gate/layout.json: the gap to moving neighbours is checked for none of its %d parts' % (key, len(mine)))
        elif mine:
            loose = parts_in_no_group(mine, groups)
            if loose:
                out.append('NO GROUP %s: %d of %d parts match no motion group of gate/layout.json, their gap to moving neighbours is not checked: %s%s'
                           % (key, len(loose), len(mine), ', '.join(loose[:6]), ' and %d more' % (len(loose) - 6) if len(loose) > 6 else ''))
    return out


def cmd_scan(root, args):
    """gate.py scan [--doc KEY]. Runs fusion_scan units on every registered document (or one) until done or 20 s have
    passed, updates gate/census.json, prints one line per new, changed and vanished part.
    Returns 0 done, 3 more calls needed (prints `NEXT n/m`), 4 Fusion not answering. A document that is not open is
    reported as unreachable and is not an error."""
    res = scan(root, doc_key=args.doc, wall_s=20.0)
    _print_scan(res, _hint_faces(root))
    if res['errors'] or res['clashes']:
        return EXIT_INFRA
    if not res['done']:
        print('NEXT %d/%d' % res['progress'])
        return EXIT_MORE
    return EXIT_PASS


SCAN_LINES = 12                 # names a scan prints per kind (new, changed, gone) before it gives the count


def _hint_faces(root):
    """The parameter process.bought_hint_faces of this project, or None when it cannot be read (then no hint)."""
    try:
        reg = gate_registry.load_default(root)
        return gate_params.values(gate_params.resolve(reg, gate_params.load_overrides(root), None), ['process.bought_hint_faces']).get('process.bought_hint_faces')
    except (GateError, KeyError):
        return None


def _print_scan(res, hint_faces=None):
    """What a scan found, short enough to read: at most SCAN_LINES names per kind, then the count. While the scan needs
    more calls nothing is said about the census: a document is merged only when its scan is complete, so counts printed
    now would be the old ones. hint_faces: new parts with at most that many faces are named as possibly bought."""
    census = res['census']
    for word, key in (('NEW', 'new'), ('CHANGED', 'changed'), ('GONE', 'gone')):
        for pid in res[key][:SCAN_LINES]:
            print('%-8s %s' % (word, pid))
        if len(res[key]) > SCAN_LINES:
            print('%-8s ... and %d more (%d in all; gate/census.json lists every part)' % (word, len(res[key]) - SCAN_LINES, len(res[key])))
    for key in res['unreachable']:
        print('UNREACHABLE  %s: document %r is not open (its parts keep their stamps; nothing new is checked)' % (key, (census['docs'].get(key) or {}).get('name')))
    for key, why in sorted(res['errors'].items()):
        print('SCAN ERROR  %s: %s' % (key, why))
    for line in res['clashes']:
        print('NAME CLASH  %s' % line)
    moved = res.get('stl_moved') or []
    for row in moved[:SCAN_LINES]:                      # "stl_names" or "stl_dir" changed in gate/layout.json: said at the scan that makes it true
        print('STL      %s: %s -> %s (gate/layout.json): the next export writes the new file%s'
              % (row['part'], row['from'], row['to'], '. %s stays in the folder: delete it once the new file is exported, or it is listed as an STL '
                                                      'without a valid stamp after the next change of the part' % row['from'] if row.get('left') else ''))
    if len(moved) > SCAN_LINES:
        print('STL      ... and %d more parts whose STL path changed (gate/census.json has every path)' % (len(moved) - SCAN_LINES))
    for doc_key, k, f in (res.get('stl_unmatched') or [])[:SCAN_LINES]:
        print('STL NAME %s: the key %r of "stl_names" names no part of this scan (a key is a part name exactly as the census shows it, or its part slug): '
              '%s is the STL of no part' % (doc_key, k, f))
    for line in over_lines(res.get('over_budget') or []):   # a scan unit over its budget is a defect of the probe like any other: never silent
        print(line)
    if not res['done']:
        print('scan: not finished, %d of %d document(s) read. The counts come with the last call' % tuple(res.get('progress') or (0, 0)))
        return
    if isinstance(hint_faces, (int, float)) and not isinstance(hint_faces, bool):
        plain = [p for p in res['new'] if isinstance((census['parts'].get(p) or {}).get('faces'), int) and census['parts'][p]['faces'] <= hint_faces]
        if plain:
            names = [census['parts'][p].get('name') or p for p in plain]
            print('HINT     %d new part(s) have %g faces or fewer (a plain cylinder, a washer, a tube). A bought part that no "not_printed" pattern '
                  'of gate/project.json names is listed as printed: %s%s' % (len(plain), hint_faces, ', '.join(repr(n) for n in names[:8]),
                                                                           ' and %d more' % (len(names) - 8) if len(names) > 8 else ''))
    for line in res.get('notes') or []:                 # once per document: linked documents, equal bodies, parts in no motion group
        print(line)
    live = [p for p, e in census['parts'].items() if not e.get('gone')]
    for key in res.get('unchanged') or []:
        print('scan: %s is as the last scan read it (its pulse is the same): not scanned again' % key)
    print('scan: %d parts, %d new, %d changed, %d gone, %d document(s) not open' % (len(live), len(res['new']), len(res['changed']), len(res['gone']), len(res['unreachable'])))


# ---- which parts ---------------------------------------------------------------------------------------------------------

def changed_parts(root, census=None):
    """Part ids of the census that have no VALID stamp for their current signature (never checked, changed since, or
    stamped under a limit that was tightened or a judge that changed since: gate_stamp.verify), sorted.
    Parts marked "gone" are left out. When the registry cannot be loaded, a stamp file that exists counts."""
    import gate_stamp
    census = census or load_census(root)
    try:
        reg = gate_registry.load_default(root)
    except GateError:
        reg = None
    out = []
    for pid in sorted(census['parts']):
        e = census['parts'][pid]
        if e.get('gone'):
            continue
        if not e.get('sig') or not os.path.isfile(gate_common.stamp_path(root, pid, e['sig'])):
            out.append(pid)
        elif reg is not None and gate_stamp._valid_file(root, reg, pid, e['sig'], census) is None:
            out.append(pid)
    return out


def part_entry(root, part, census=None):
    """The census entry of a part. A part made by code that no scan has listed yet (a generator of gate/layout.json) gets
    an entry made here, so its STL can be checked and stamped with no Fusion at all. GateError for an unknown part."""
    census = census if census is not None else load_census(root)
    entry = census['parts'].get(part)
    if entry is not None:
        return entry
    for g in load_layout(root).get('generators') or []:
        if g.get('part') == part:
            key, name = gate_common.split_part_id(part)
            return {'doc': key, 'name': name, 'source': 'generated', 'generator': g.get('source'), 'stl': g.get('stl'), 'sig': None, 'prev_sig': None, 'gone': False}
    raise GateError('unknown part %r: it is not in gate/census.json (gate.py scan discovers parts; a part made by code is registered under "generators" in gate/layout.json)' % (part,))


# ---- judging -------------------------------------------------------------------------------------------------------------

def _prev_facts(root, part, entry, sig, probe):
    """The facts of one probe at the part's previous CHECKED signature: the census "prev_sig" when it was checked, else
    the newest earlier signature (census "history") that has facts of this probe. None when there is nothing to compare
    with."""
    for old in [entry.get('prev_sig')] + list(reversed(entry.get('history') or [])):
        if old and old != sig:
            got = _probe_facts(load_facts(root, part, old), probe)
            if got is not None:
                return got
    return None


def _mates(root, census, entry, prev):
    """What regression.feature_lost needs to know of the printed parts that stood on the axes of the holes of a
    part's last checked census ("prev"): {part id: {"changed": its signature differs from its last checked one,
    "lines": [[point, axis] of each of its census holes, in the document frame]}}. A hole that moved with its
    fasteners is only "moved, nothing lost" when such a part moved its own hole along (gate_judges._mate_followed)."""
    out = {}
    pids = sorted({str(c.get('part')) for h in ((prev or {}).get('holes') or []) if isinstance(h, dict)
                   for c in (h.get('coaxial') or []) if isinstance(c, dict) and c.get('kind') == 'printed' and c.get('part')})
    for pid in pids:
        e = (census.get('parts') or {}).get(pid)
        if not isinstance(e, dict) or not e.get('sig'):
            continue
        cen = _probe_facts(load_facts(root, pid, e['sig']), 'fusion_census') or {}
        m = e.get('matrix')
        lines = []
        for h in cen.get('holes') or []:
            a = h.get('at') if isinstance(h, dict) and isinstance(h.get('at'), dict) else (h.get('anchor') if isinstance(h, dict) else None)
            if not (isinstance(a, dict) and isinstance(a.get('point'), list) and isinstance(a.get('axis'), list) and len(a['point']) == 3 and len(a['axis']) == 3):
                continue
            if isinstance(m, list) and len(m) == 16:
                pt = [round(m[4 * r] * a['point'][0] + m[4 * r + 1] * a['point'][1] + m[4 * r + 2] * a['point'][2] + m[4 * r + 3], 4) for r in range(3)]
                ax = [round(m[4 * r] * a['axis'][0] + m[4 * r + 1] * a['axis'][1] + m[4 * r + 2] * a['axis'][2], 6) for r in range(3)]
            else:
                pt, ax = list(a['point']), list(a['axis'])
            lines.append([pt, ax])
        out[pid] = {'changed': bool(e.get('prev_sig') and e.get('prev_sig') != e.get('sig')), 'lines': sorted(lines)}
    return out


_STL_SHA = {}


def _stl_is_export(root, part, entry):
    """Can the STL the census names for the part be the export of its present signature? False when the part went
    through the gate's export before and no stamp of entry["sig"] holds the file's bytes: before a re-export the file,
    and the mesh a project places from it, is the earlier geometry. A part the gate never stamped (an STL of an older
    exporter) has no stamp to tell: once an export of this signature has measured its STL (the stl_mesh facts of the
    geometry, written by the first `gate.py export` of it, also one that stopped at a question) and the file in the
    folder is another geometry, it is the earlier geometry too. Before any export of the signature the file is taken as
    it is: nothing says otherwise, and the export judges the geometry it writes (exporting())."""
    import gate_stamp
    path = os.path.join(root, *str(entry.get('stl') or '').replace(os.sep, '/').split('/'))
    stamps = gate_stamp.all_stamps(root, part)
    if not stamps:
        exported = _exported_geometry(root, part, entry.get('sig'))
        if not exported:
            return True
        try:
            st = os.stat(path)
        except OSError:
            return False
        key = ('geom', os.path.abspath(path), st.st_mtime_ns, st.st_size)
        if key not in _STL_SHA:
            if len(_STL_SHA) > 512:
                _STL_SHA.clear()
            try:
                _STL_SHA[key] = gate_common.sibling('meshid').stl_ids(path)['geom_hash']
            except (ValueError, OSError, IndexError):
                _STL_SHA[key] = None
        return _STL_SHA[key] == exported
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if key not in _STL_SHA:
        if len(_STL_SHA) > 512:
            _STL_SHA.clear()
        _STL_SHA[key] = gate_common.sha256_file(path)
    sha = _STL_SHA[key]
    return any(s.get('sig') == entry.get('sig') and s.get('stl_sha256') == sha for s in stamps)


_EXPORTED = {}


def _exported_geometry(root, part, sig):
    """meshid.geom_hash of the STL an export of this signature wrote (the stl_mesh facts of the geometry), or None
    when no export of it measured one. Read once per state of the facts file."""
    if not sig:
        return None
    path = gate_common.facts_path(root, part, sig)
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if key not in _EXPORTED:
        if len(_EXPORTED) > 512:
            _EXPORTED.clear()
        e = ((load_facts(root, part, sig).get('probes') or {}).get('stl_mesh') or {})
        f = e.get('facts') if isinstance(e, dict) else None
        _EXPORTED[key] = f.get('geom_hash') if isinstance(f, dict) and isinstance(f.get('geom_hash'), str) else None
    return _EXPORTED[key]


_EXPORTING = {}               # part id -> the STL an export of it is about to write (exporting())


@contextlib.contextmanager
def exporting(part, stl):
    """While active, print_frame() of `part` judges the geometry of `stl` (the STL `gate.py export` is about to write,
    in quarantine) instead of the STL in the folder: the judges, the fact answers and the stamp's inputs of an export
    are about the geometry it stamps. A placed mesh that is the STL still in the folder, and not this one, is "earlier
    geometry" (probes.project_bed.placed_now). stl None: nothing changes."""
    before = _EXPORTING.get(part)
    if stl:
        _EXPORTING[part] = os.path.abspath(stl)
    try:
        yield
    finally:
        if before is None:
            _EXPORTING.pop(part, None)
        else:
            _EXPORTING[part] = before


def print_frame(root, part, entry, layout=None):
    """How one part lies on the bed, for the judges that count layers (ctx["print"]):
    {"up": unit vector of the print axis in the PART frame, pointing away from the bed, or None,
     "bed": the height of the bed face along "up" (the lowest corner of the part's bounding box), or None,
     "source": where the orientation comes from, "why": text when "up" is None,
     "from": "project" | "table" | "earlier geometry" | None, "frames": [one frame per face it lies on]}.

    The bed face is measured on the saved .3mf: the object whose mesh is the part's STL turned as a rigid body lies on
    the face that turn sends down (probes.project_bed). Only for a project that does not place the part yet does the
    build table say it (probes.project_consistency.orientations: the `orient` value bambu3mf.build() takes). Either
    way "source" names the file, the object and the plate. That direction is in the
    frame of the STL, which is the document frame; the census entry's "matrix" (part frame -> document frame, from the
    scan) turns it into the part frame, where the census holds its holes. Unknown, and said so in "why": no build table
    names the part; a table is computed, not a literal; an orient value that cannot be read; a census entry without its
    matrix (scanned by an older copy of the tools: scan again).

    Each placement counts. When the registered projects lay the part on different faces (a production and a test
    project that share the STL folder), "frames" holds one frame per face ({"up", "bed", "source", "from"}), "up" and
    "bed" are None and "why" names the faces: judge_part judges every rule that reads the print frame once per face
    and keeps every finding, so the worst placement decides. "from" is "project" only when every face is measured on
    this geometry.

    While an export of the part is under way (exporting()), the geometry judged is the STL it is about to write: a mesh
    placed from the STL still in the folder is the earlier geometry of the part ("from": "earlier geometry"), its face
    taken from that placement."""
    from probes import _geom, project_consistency
    layout = layout if layout is not None else load_layout(root)
    out = {'up': None, 'bed': None, 'source': None, 'why': None, 'from': None}
    stl = entry.get('stl')
    if not stl:
        out['why'] = 'the census names no STL for this part'
        return out
    geometry = _EXPORTING.get(part)
    if geometry:
        rows, problems = project_consistency.orientations(root, layout, stl, geometry=geometry)
    else:
        rows, problems = project_consistency.orientations(root, layout, stl)
    if not rows:
        tables = [e.get('build_table') for e in layout.get('projects') or [] if isinstance(e, dict) and e.get('build_table')]
        unplaced = 'no object of a saved .3mf project is %s turned as a rigid body' % stl       # the measured face comes first
        if not tables:
            out['why'] = 'no registered .3mf project names a build table ("build_table" in gate/layout.json), and %s' % unplaced
        elif problems:
            out['why'] = '%s; %s' % ('; '.join(problems), unplaced)
        else:
            out['why'] = 'no build table of a registered .3mf project builds an object from %s, and %s' % (stl, unplaced)
        return out
    bad = [r for r in rows if r['down'] is None]
    if bad:
        out['why'] = 'the orientation %r of %s in %s cannot be read' % (bad[0]['orient'], bad[0]['object'], bad[0]['table'])
        return out
    m = _geom.IDENTITY if entry.get('source') == 'generated' else entry.get('matrix')     # an STL made by code is its own frame
    if not (isinstance(m, (list, tuple)) and len(m) == 16 and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in m)):
        out['why'] = 'the census holds no pose of this part (its matrix): scan the document again'
        return out
    exported = None

    def one(group):
        nonlocal exported
        f = {'source': '%s: %s on plate "%s", orient %r' % (group[0]['table'], group[0]['object'], group[0]['plate'], group[0]['orient'])}
        sources = {r.get('source') or 'table' for r in group}
        f['from'] = 'project' if sources == {'project'} else 'table' if 'table' in sources else 'earlier geometry'      # gate_attest bed_face_measured
        if f['from'] == 'project' and not geometry and entry.get('source') != 'generated':
            if exported is None:
                exported = _stl_is_export(root, part, entry)
            if not exported:
                f['from'] = 'earlier geometry'      # the STL on disk (and the mesh placed from it) is not the export of this signature yet
        up_doc = tuple(-c for c in group[0]['down'])
        up = _geom.v_unit(_geom.mat_vector(_geom.mat_inverse(m), up_doc))
        f['up'] = [round(c, 6) for c in up]
        box = entry.get('bbox')
        try:
            lo, hi = box
            corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            f['bed'] = round(min(_geom.v_dot(c, up) for c in corners), 6)
        except (TypeError, ValueError, IndexError):
            f['bed'] = None
        return f
    groups = {}
    for r in rows:
        groups.setdefault(tuple(round(c, 4) + 0.0 for c in r['down']), []).append(r)
    frames = [one(groups[k]) for k in sorted(groups)]
    out['frames'] = frames
    if len(frames) == 1:
        out.update({k: frames[0][k] for k in ('up', 'bed', 'source', 'from')})
        return out
    froms = {f['from'] for f in frames}
    out['from'] = 'project' if froms == {'project'} else 'earlier geometry' if 'earlier geometry' in froms else 'table'
    out['source'] = '; '.join(f['source'] for f in frames)
    out['why'] = ('the registered projects lay it on %d different faces (%s): each face is judged' % (len(frames), '; '.join(sorted({'%s in %s: %r' % (r['object'], r['table'], r['orient']) for r in rows}))))
    return out


_TABLE_DIGESTS = {}


def tables_digest(tables, read=None):
    """16 hex over what of the tables a judgement read: `read` the names of the tables its judge looked at (TableReads),
    None (a row of an older gate, or a judge that read every table) or "*" in it: all of them. A row of gate/owner/tables.json
    re-judges only the checks whose judge read that table."""
    want = None if read is None or '*' in read else tuple(sorted(read))
    key = (id(tables), want)
    hit = _TABLE_DIGESTS.get(key)
    if hit is not None and hit[0] is tables:
        return hit[1]
    t = tables if isinstance(tables, dict) else {}
    value = gate_common.digest(tables) if want is None else gate_common.digest({'names': sorted(t), 'read': {n: t.get(n) for n in want}})
    if len(_TABLE_DIGESTS) > 4096:
        _TABLE_DIGESTS.clear()
    _TABLE_DIGESTS[key] = (tables, value)
    return value


class TableReads(dict):
    """The tables handed to a judge (ctx["tables"]), recording the names of the tables it looks at ("read"): a judge
    is a pure function of its facts, parameters and ctx, so a table it never looked at cannot change its findings.
    Walking the tables (keys, items, values, a copy) counts as reading all of them ("*")."""

    def __init__(self, tables):
        dict.__init__(self, tables if isinstance(tables, dict) else {})
        self.read = set()

    def __getitem__(self, k):
        self.read.add(k)
        return dict.__getitem__(self, k)

    def get(self, k, default=None):
        self.read.add(k)
        return dict.get(self, k, default)

    def __contains__(self, k):
        self.read.add(k)
        return dict.__contains__(self, k)

    def _all(self):
        self.read.add('*')

    def __iter__(self):
        self._all()
        return dict.__iter__(self)

    def keys(self):
        self._all()
        return dict.keys(self)

    def items(self):
        self._all()
        return dict.items(self)

    def values(self):
        self._all()
        return dict.values(self)

    def copy(self):
        self._all()
        return dict(dict.items(self))

    def __reduce__(self):
        self._all()
        return (dict, (dict(dict.items(self)),))


_READS_PRINT = {}


def reads_print(name):
    """Does the judge `name` (or a private helper it reaches) read ctx["print"]? Only such a judge is judged once per
    face a part lies on (print_frame "frames")."""
    import gate_judges
    fn = gate_judges.JUDGES.get(name)
    if fn is None:
        return False
    if (name, fn) not in _READS_PRINT:
        _READS_PRINT[(name, fn)] = any(("ctx['print']" in src or "ctx.get('print')" in src) for src in gate_judges._judge_sources(fn))
    return _READS_PRINT[(name, fn)]


def judge_inputs(root, reg, part, entry):
    """{"print": 16 hex over print_frame() of the part today, "tables": 16 hex over all the tables today}: the two
    judgement inputs that are no facts of the part. judge_part keeps them beside each judged row (and a stamp in each
    check), so that a part turned on its bed or a table the owner changed is judged again (judgement_out_of_date,
    gate_stamp.verify). A row that holds "tables_read" is held against tables_digest() of only those tables."""
    try:
        frame = print_frame(root, part, entry)
    except Exception as e:
        frame = {'up': None, 'bed': None, 'source': None, 'why': 'the print orientation could not be worked out (%s: %s)' % (type(e).__name__, e)}
    return {'print': frame_digest(frame), 'tables': tables_digest(reg['tables'])}


def inputs_differ(row, today, tables):
    """The names of the inputs ("print", "tables") a judged row or a stamp's check holds that differ from today's
    (judge_inputs; the tables held against the ones the row read, tables_digest)."""
    out = []
    if 'print' in row and row['print'] != today['print']:
        out.append('print')
    if 'tables' in row and row['tables'] != tables_digest(tables, row.get('tables_read') if isinstance(row.get('tables_read'), list) else None):
        out.append('tables')
    return out


def frame_digest(frame):
    """16 hex over what of a print frame can change a verdict: the print axis and the height of the bed face (its texts
    only say where it comes from), of every face when the part lies on several."""
    f = frame if isinstance(frame, dict) else {}
    frames = f.get('frames') if isinstance(f.get('frames'), list) else []
    if len(frames) > 1:
        return gate_common.digest({'frames': sorted(([x.get('up'), x.get('bed')] for x in frames), key=json.dumps)})
    return gate_common.digest({'up': f.get('up'), 'bed': f.get('bed')})


def judge_part(root, reg, resolved, part, sig=None, stage='design', intent=None, target=None, reuse=True, rules=None, store=True):
    """Judge the stored facts of one part with today's judges and parameters and store the result.
    reuse: a stored judgement made from exactly what the judge would read now, by the same judging code
    (judging_code) and rule, is taken over (False: every judge runs again, `run --fresh`).

    For every rule with a judge of that stage that applies to the part: take the facts of its probe from the part's
    facts file (None when the unit did not run), call gate_judges.run_judge(rule, facts, gate_params.values(resolved,
    rule params), ctx) and store the findings with gate_common.save_judged (judge hash and parameter values beside
    them). ctx is built as gate_judges describes ("census", "prev" from the facts file of the part's "prev_sig",
    "print" from print_frame()).
    regression.unintended gets {"changed": [...]} built from the census.

    sig None = the part's current census signature. Returns {rule id: [findings]}.
    rules: judge only these rule ids; store False: store nothing (gate_stamp.verify judges a stamped part again in
    memory when its print orientation or the tables changed)."""
    import gate_judges
    census = load_census(root)
    entry = part_entry(root, part, census)
    if entry.get('source') == 'generated' and sig and entry.get('sig') != sig:
        # a part made by code is "scanned" by reading its STL: the census follows the file that is being judged
        _merge_generators(root, census, load_layout(root), None, {'new': [], 'changed': [], 'gone': [], 'clashes': []}, only=part)
        write_json(gate_common.census_path(root), census)
        entry = census['parts'].get(part, entry)
    sig = sig or entry.get('sig')
    if not sig:
        raise GateError('part %r has no signature yet (a generated part whose STL is missing?)' % (part,))
    data = load_facts(root, part, sig)
    census_facts = _probe_facts(data, 'fusion_census')
    out, frame, rows, dg = {}, None, {}, {}

    def digest_of(name, value):
        if name not in dg:
            dg[name] = gate_common.digest(value)
        return dg[name]
    for rule in _stage_rules(reg, stage, entry.get('source', 'fusion')):
        if rules is not None and rule['id'] not in rules:
            continue
        probe, name = rule['check']['probe'], rule['check']['judge']
        if probe == 'fusion_scan':               # a document unit: its facts for one part come from the census
            stamped = os.path.isfile(gate_common.stamp_path(root, part, sig))
            facts = {'changed': [] if stamped else [{'part': part, 'sig': sig, 'prev_sig': entry.get('prev_sig')}]}
        else:
            facts = _probe_facts(data, probe)
        params = gate_params.values(resolved, rule.get('params') or [])
        if frame is None:
            try:
                frame = print_frame(root, part, entry)
            except Exception as e:                   # what could not be worked out is unknown, and the judge says so
                frame = {'up': None, 'bed': None, 'source': None, 'why': 'the print orientation could not be worked out (%s: %s)' % (type(e).__name__, e)}
        tables = reg['tables']
        ctx = {'part': part, 'rule': rule['id'], 'tables': tables, 'target': target, 'census': census_facts,
               'prev': None if probe == 'fusion_scan' else _prev_facts(root, part, entry, sig, probe),
               'intent': list(intent) if intent is not None else None, 'print': frame}
        if name == 'regression_feature_lost' and ctx['prev'] is not None:
            ctx['mates'], ctx['matrix'] = _mates(root, census, entry, ctx['prev']), entry.get('matrix')   # did the parts on its axes move along
        stored = (data.get('probes') or {}).get(probe)
        if facts is None and isinstance(stored, dict) and str(stored.get('error') or '').startswith(OVER_BUDGET):
            ctx['no_facts'] = _first_line(stored['error'])       # the unit was given up: the NOT_RUN row says so
        # everything this judge reads, as one key: when the stored judgement was made from the same, it is taken over
        # (a judge is a pure function of its facts, parameters and ctx: gate_judges.run_judge)
        # judging_code(): the files the judges run (module constants, shared helpers, finding ids), not only the
        # judge's own source; digest_of('rule'): the rule as the registry has it today (its fix order, its texts);
        # of the tables only those the judge looked at the last time (tables_read): a judge looks at what decides
        jh = _judge_hash(name)

        def key_for(read):
            return gate_common.digest([rule['id'], jh, judging_code(), gate_common.digest(rule), params, digest_of('facts:' + probe, facts), tables_digest(tables, read),
                                       digest_of('census', census_facts), digest_of('prev:' + probe, ctx['prev']), digest_of('print', frame),
                                       {k: v for k, v in ctx.items() if k not in ('tables', 'census', 'prev', 'print')}])
        old = (data.get('judged') or {}).get(rule['id'])
        if reuse and isinstance(old, dict) and old.get('judge_hash') == jh and old.get('params') == params and isinstance(old.get('findings'), list) \
                and old.get('input') == key_for(old.get('tables_read') if isinstance(old.get('tables_read'), list) else None):
            out[rule['id']] = old['findings']
            continue
        reads = TableReads(tables)
        frames = frame.get('frames') if isinstance(frame, dict) and isinstance(frame.get('frames'), list) and len(frame['frames']) > 1 else None
        if frames and reads_print(name):
            findings, seen = [], set()
            for fr in frames:                        # every face the part lies on is judged: the worst placement decides
                for f in gate_judges.run_judge(rule, facts, params, dict(ctx, tables=reads, print=fr)):
                    k = json.dumps([f.get('verdict'), f.get('anchor'), f.get('context'), f.get('measure')], sort_keys=True, default=str)
                    if k in seen:
                        continue
                    seen.add(k)
                    findings.append(dict(f, text='%s (placed in %s)' % (f.get('text'), fr.get('source'))))
        else:
            findings = gate_judges.run_judge(rule, facts, params, dict(ctx, tables=reads))
        read = sorted(reads.read)
        rows[rule['id']] = {'judge_hash': jh, 'params': params, 'findings': findings, 'time': gate_common.now(), 'input': key_for(read), 'code': judging_code(),
                            'print': frame_digest(frame), 'tables': tables_digest(tables, read), 'tables_read': read}
        out[rule['id']] = findings
    if not store:
        return out
    if rows:
        gate_common.save_judged_many(root, part, sig, rows)     # one write per part, not one per rule
    import gate_attest
    gate_attest.record_fact_answers(root, reg, part, sig)      # the standing questions the facts of this geometry answer
    return out


_JUDGE_HASHES = {}             # (name, function) -> hash: reading a judge's source 1600 times is too slow for the session-start digest
_JUDGING = {}
JUDGING_FILES = ('gate_judges.py', 'gate_features.py', 'gate_common.py', os.path.join('probes', '_geom.py'), os.path.join('probes', '_geom_rings.py'))


def judging_code():
    """16 hex over the text of the files the judges run (JUDGING_FILES: their module constants, the public helpers
    such as finding(), the finding ids and context hashes of gate_features, the classification of _geom). A stored
    judgement is taken over only when it was made by this code (judge_part); judge_hash() alone covers a judge's own
    source and the private helpers it calls, which is what a STAMP rests on. Read once per process."""
    if 'v' not in _JUDGING:
        here = os.path.dirname(os.path.abspath(__file__))
        parts = []
        for name in JUDGING_FILES:
            try:
                with open(os.path.join(here, name), 'rb') as f:
                    parts.append(hashlib.sha256(f.read()).hexdigest())
            except OSError:
                parts.append(None)
        _JUDGING['v'] = gate_common.digest(parts)
    return _JUDGING['v']


def _judge_hash(name):
    import gate_judges
    fn = gate_judges.JUDGES.get(name)
    if fn is None:
        return None
    if (name, fn) not in _JUDGE_HASHES:
        _JUDGE_HASHES[(name, fn)] = gate_judges.judge_hash(name)
    return _JUDGE_HASHES[(name, fn)]


def _waivers(root):
    data = read_json(gate_path(root, 'owner', 'waivers.json'), {})
    return list(data.get('waivers') or []) if isinstance(data, dict) else []


def _with_world(f, matrix):
    """The finding with "world": its anchor point in the frame of the DOCUMENT (the census matrix of the part: component
    -> document, translation in mm), when the part is placed off the document origin. The anchor itself stays in the
    part's own frame (the id, the waivers and the answers are tied to it); "world" is only what a reader needs to find
    the place in the assembly, and nothing reads it but the text of a row."""
    try:
        p = (f.get('anchor') or {}).get('point')
        if not (isinstance(matrix, (list, tuple)) and len(matrix) == 16 and isinstance(p, (list, tuple)) and len(p) == 3):
            return f
        m = [float(v) for v in matrix]
        if m == [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]:
            return f
        x, y, z = (float(v) for v in p)
        w = [round(m[4 * i] * x + m[4 * i + 1] * y + m[4 * i + 2] * z + m[4 * i + 3], 2) + 0.0 for i in range(3)]
        return dict(f, world=w)
    except (TypeError, ValueError):
        return f


def part_verdict(root, reg, part, sig=None, stages=('design',), defer_inherited=False):
    """What the stored judgements of one part add up to, with no Fusion call and no judging.

    defer_inherited  False (what a stamp needs: `gate.py export` calls it so): every standing question counts.
                     True (run, status, attest list): the questions of an INHERITED part (gate_report: the audit
                     judged this geometry, it has not changed, it has no stamp) are not asked: the standing ones of
                     the part and the ones a finding carries. They are returned as "deferred_questions" (the findings
                     as "deferred_asked"), and a part with nothing else open has the verdict "inherited".

    Returns {"part", "sig", "verdict": "pass" | "pass_attested" | "fail" | "attest" | "incomplete" | "unreachable",
             "open": [findings that block: no valid waiver, no "ask"],
             "asked": [findings with "ask" that no answer closes yet],
             "answered": [findings an attest answer closed], "waived": [findings a valid waiver closed],
             "info": [INFO findings], "not_run": [rule ids of these stages with nothing stored for this signature],
             "waivers": the rows of gate_features.apply_waivers(), "questions": open part-level questions
             (gate_attest.questions_for_part), "attested": n answers the verdict rests on}.
    "incomplete": a rule has nothing stored, or every open row is a check that could not decide (NOT_RUN): nothing is
    known to be wrong, and nothing has passed; "fail": a row in "open" that was measured (FAIL, GROW, BLOCKED);
    "attest": nothing open, a question is; "pass_attested": nothing open, answers were needed; "pass": nothing at all.
    An answer the gate gave itself from the facts (gate_attest.fact_answer) is not counted in "attested".

    Every finding carries its "id" (gate_features.finding_id). Also returned:
      "inherited"      findings that are items of gate/owner/triage.json (same rule, anchor and context): they do not
                       count as open here; gate_report.inherited_block() says which of them stop the part's export
      "ask_questions"  the open questions of the "asked" findings; "all_questions": every question, answered or not
      "stale"          rule ids whose stored judgement was made by an older version of the judge (run --offline)
      "state"          "new" | "changed" | "unchanged" | "inherited" | "unreachable" for the report
      "by_facts"       the part-level questions the facts answered
      "deferred_questions", "deferred_asked", "inherited_part"   see defer_inherited
      "proposed_part"  an audit judged this geometry after the owner adopted his list and he has not taken that audit
                       over (gate_report.is_proposed): judged as any part, never inherited by itself
    "unreachable": the part's document is not open and a rule has nothing stored (old stamps stay valid)."""
    import gate_attest, gate_features, gate_judges, gate_report
    census = load_census(root)
    entry = part_entry(root, part, census)
    sig = sig or entry.get('sig')
    source = entry.get('source', 'fusion')
    data = load_facts(root, part, sig) if sig else {'probes': {}, 'judged': {}}
    findings, not_run, stale = [], [], []
    for stage in stages:
        for rule in _stage_rules(reg, stage, source):
            j = (data.get('judged') or {}).get(rule['id'])
            if not isinstance(j, dict):
                not_run.append(rule['id'])
                continue
            name = rule['check']['judge']
            if name in gate_judges.JUDGES and j.get('judge_hash') != _judge_hash(name):
                stale.append(rule['id'])
            findings += [_with_world(dict(f, id=gate_features.finding_id(f)), entry.get('matrix')) for f in j.get('findings') or [] if f.get('part', part) == part]
    census_facts = _probe_facts(data, 'fusion_census')
    features = ((census_facts.get('holes') or []) + (census_facts.get('openings') or [])) if census_facts else None
    info = [f for f in findings if f.get('verdict') == 'INFO' and not f.get('ask')]
    live = [f for f in findings if not (f.get('verdict') == 'INFO' and not f.get('ask'))]
    left, waived, rows = gate_features.apply_waivers(live, _waivers(root), features, part)
    triage = gate_report.triage_of(root, part)
    inherited, fresh = [], []
    for f in left:
        item = gate_report.triage_match(triage, f)
        if item is not None:
            inherited.append(dict(f, triage=item.get('id'), decision=item.get('decision')))
        else:
            fresh.append(f)
    questions = gate_attest.questions_for_part(root, reg, part, sig, fresh, stages) if sig else []
    questions_off = gate_common.questions_off(root)
    by_id = {q['q']: q for q in questions}
    open_, asked, answered, ask_questions = [], [], [], []
    for f in fresh:
        if not f.get('ask'):
            open_.append(f)
            continue
        q = by_id.get(gate_attest.question_id(f['ask'], part, f.get('anchor'), f.get('context')))
        if q is not None and q.get('answer'):
            answered.append(dict(f, answer=q['answer']))
        elif questions_off:
            (info if f.get('verdict') == 'INFO' else open_).append(f)
        else:
            asked.append(f)
            if q is not None and q not in ask_questions:
                ask_questions.append(q)
    part_questions = [q for q in questions if not q.get('finding')]
    open_questions = [q for q in part_questions if not q.get('answer')]
    by_facts = [q for q in part_questions if q.get('answer') and q['answer'].get('kind') == gate_attest.FACTS]
    attested = len(answered) + sum(1 for q in part_questions if q.get('answer')) - len(by_facts)
    doc_open = (census['docs'].get(entry.get('doc')) or {}).get('open', True) is not False or source != 'fusion'
    stamped = bool(sig) and os.path.isfile(gate_common.stamp_path(root, part, sig))
    base = gate_report.baseline(root)
    inherited_part = bool(sig) and not stamped and gate_report.is_inherited(root, part, sig, base)
    proposed_part = bool(sig) and not stamped and not inherited_part and gate_report.is_proposed(root, part, sig, base)
    deferred, deferred_asked = [], []
    if defer_inherited and inherited_part:
        deferred, open_questions = open_questions + ask_questions, []
        deferred_asked, asked, ask_questions = asked, [], []
    if not_run and not doc_open:
        verdict = 'unreachable'
    elif any(f.get('verdict') != 'NOT_RUN' for f in open_):
        verdict = 'fail'
    elif not_run or open_:
        verdict = 'incomplete'
    elif asked or open_questions:
        verdict = 'attest'
    elif defer_inherited and inherited_part:
        verdict = 'inherited'
    else:
        verdict = 'pass_attested' if attested else 'pass'
    state = ('unreachable' if not doc_open else 'unchanged' if stamped else 'inherited' if inherited_part
             else 'changed' if entry.get('prev_sig') else 'new')
    return {'part': part, 'sig': sig, 'verdict': verdict, 'open': open_, 'asked': asked, 'answered': answered, 'waived': waived,
            'info': info, 'not_run': not_run, 'waivers': rows, 'questions': open_questions, 'attested': attested,
            'inherited': inherited, 'ask_questions': ask_questions, 'all_questions': questions, 'stale': stale, 'state': state,
            'by_facts': by_facts, 'deferred_questions': deferred, 'deferred_asked': deferred_asked, 'inherited_part': inherited_part,
            'proposed_part': proposed_part}


def exit_code(verdicts, audit=False, more=False):
    """The exit code for a list of part_verdict() results: 3 when more calls are needed, else 1 when any part is "fail"
    or "incomplete", else 2 when any is "attest", else 0. With audit: the parts the audit inherits (verdict key
    "inherited_part") do not count; a part that changed after the audit, or that has a stamp, counts as in any run.

    A part whose open rows are all NOT_RUN is "incomplete" and gives 1 like a failed one: a check that could not decide
    has not passed, so the run is no pass and no question round. What differs is the text: the summary line splits the
    open rows by verdict, and `gate.py run` says in its last lines that nothing failed and what makes the checks run.
    An "inherited" part (nothing new open, its standing questions wait for its export) does not make a run fail."""
    if more:
        return EXIT_MORE
    if audit:                                    # an audit records what exists: an inherited part never blocks it
        verdicts = [v for v in verdicts if v.get('inherited_part') is False]
    kinds = {v.get('verdict') for v in verdicts}
    if kinds & {'fail', 'incomplete'}:
        return EXIT_BLOCK
    if 'attest' in kinds:
        return EXIT_ATTEST
    return EXIT_PASS


# ---- gate.py run ---------------------------------------------------------------------------------------------------------

def _settled(root, census):
    """Inherited parts of the owner's adopted list whose present geometry is judged: the audit looked at them, the owner
    took the audit over, and they have not changed since. They have no stamp, and still a run of "what changed" has
    nothing to do on them: measuring forty parts again on every run would be the audit all over. `--all`, `--part`,
    `--audit` and `--offline` take them all the same. A part of an audit the owner has NOT adopted is not settled: its
    rows are still ordinary open findings, and a run shows them."""
    import gate_report
    base, out = {p: e for p, e in gate_report.baseline(root).items() if e.get('adopted')}, set()
    for pid, e in census['parts'].items():
        if (not e.get('gone') and e.get('sig') and gate_report.is_inherited(root, pid, e['sig'], base)
                and (read_json(gate_common.facts_path(root, pid, e['sig']), {}) or {}).get('judged')):
            out.add(pid)
    return out


def judgement_out_of_date(root, reg, part, sig, resolved, stages=('design',), entry=None):
    """Rule ids of these stages whose stored judgement at this signature was made by another judge, by other judging
    code (judging_code), with other parameter values than today's, or with another print orientation or other tables
    than today's (judge_inputs: the part was turned on its bed, the owner changed gate/owner/tables.json): judge_part
    judges them again, offline; their facts may be as they were."""
    import gate_judges
    entry = entry if entry is not None else (load_census(root)['parts'].get(part) or {})
    judged = load_facts(root, part, sig).get('judged') or {}
    today = [None]

    def inputs_moved(j):
        if not any(k in j for k in ('print', 'tables')):
            return False                                # a row of an older gate: its other keys decide
        if today[0] is None:
            today[0] = judge_inputs(root, reg, part, entry)
        return bool(inputs_differ(j, today[0], reg['tables']))
    out = []
    for stage in stages:
        for rule in _stage_rules(reg, stage, entry.get('source', 'fusion')):
            j = judged.get(rule['id'])
            name = rule['check']['judge']
            if not isinstance(j, dict) or (name in gate_judges.JUDGES and j.get('judge_hash') != _judge_hash(name)) \
                    or ('code' in j and j['code'] != judging_code()) or j.get('params') != gate_params.values(resolved, rule.get('params') or []) \
                    or inputs_moved(j):
                out.append(rule['id'])
    return out


def unchecked(stale):
    """Part ids of a stale_units() result with at least one unit that must run again, sorted."""
    return sorted(p for p, units in stale.items() if any(w is not None for w in units.values()))


def _select(root, args, census, intent, stale=None, reg=None, resolved=None):
    """The parts of a run (what it reports), in order. `--changed`: every part with no valid stamp for its present
    signature (changed_parts), less the inherited parts of the owner's adopted list (_settled), as before; with `stale`
    (a run that asks Fusion) also every part with a unit whose stored facts no longer hold (stale_units: a stamped or
    settled part whose neighbour moved is checked again) and every part whose stored judgement is out of date. Which
    of their units are MEASURED is not decided here: new_plan() runs only the stale ones, the others are reused."""
    live = sorted(p for p, e in census['parts'].items() if not e.get('gone'))
    changed = changed_parts(root, census)
    if not args.audit and not args.offline:
        settled = _settled(root, census)
        changed = [p for p in changed if p not in settled]
    if stale is not None:
        closed = {k for k, d in census['docs'].items() if d.get('open') is False}       # nothing can be measured there: its stamps stay
        stale = {p: u for p, u in stale.items() if census['parts'][p].get('doc') not in closed}
        more = set(unchecked(stale))
        if reg is not None and resolved is not None:
            more |= {p for p in stale if p not in more and judgement_out_of_date(root, reg, p, census['parts'][p]['sig'], resolved, entry=census['parts'][p])}
        changed = sorted(set(changed) | more)
    if args.part:
        unknown = [p for p in args.part if p not in live]
        if unknown:
            raise GateError('unknown part %s. Parts of the census: %s' % (', '.join(unknown), ', '.join(live) or 'none (run gate.py scan)'))
        parts = list(dict.fromkeys(args.part))
    elif args.all:
        parts = live
    else:
        parts = changed
    if intent is not None:                       # a stated intent: every part that changed is looked at, named or not
        parts = parts + [p for p in changed if p not in parts]
    return parts


def _request(args):
    out = {'select': 'part' if args.part else 'all' if args.all else 'changed', 'part': sorted(args.part or []), 'audit': bool(args.audit)}
    if getattr(args, 'fresh', False):
        out['fresh'] = True                       # measured again: never the plan of a run that takes stored facts over
    if getattr(args, 'target', None):
        out['for'] = args.target                  # another target is another run: the units are sent with its numbers
    if getattr(args, 'only', None):
        out['only'] = sorted(args.only)           # a partial run is never the plan of a full one
    return out


def only_probes(reg, names):
    """The probe names a `--only` list stands for: "inserts" is fusion_inserts. GateError for a name no rule uses."""
    known = {r['check']['probe'] for r in gate_registry.rules(reg) if (r.get('check') or {}).get('needs') == 'fusion'} - {'fusion_scan'}
    out = set()
    for n in names or []:
        full = n if n in known else 'fusion_' + n
        if full not in known:
            raise GateError('--only %s: no such probe (known: %s)' % (n, ', '.join(sorted(p[len('fusion_'):] for p in known))))
        out.add(full)
    return out


def _judge_all(root, reg, resolved, parts, census, intent, target, unreachable_docs, with_export=False, pending=(), audit_run=None, only=None, reuse=True):
    """Judge every part that can be judged and return the verdicts of all of them, in order. A part of `pending` still
    has units to run: it is judged when they are done, until then its verdict comes from what was stored before.
    audit_run: the id of an audit run; each part it judges is noted in the baseline of inherited parts
    (gate_report.baseline_add: written once per part). A part one of whose units was given up over its budget
    (given_up) is NOT noted: nothing judged the rules of that unit, so the audit has not looked at the part, it stays
    an ordinary part, and its NOT_RUN rows count (exit 1). The verdicts are the ones of a run: the standing questions
    of an inherited part wait (part_verdict with defer_inherited).
    only: judge only these parts (the ones whose units finished in this call); the verdicts of the others are read
    from what is stored. None = judge every part that can be judged."""
    import gate_report
    verdicts = []
    for p in parts:
        entry = census['parts'][p]
        reachable = entry.get('doc') not in unreachable_docs or entry.get('source', 'fusion') != 'fusion'
        stages = ('design',)
        if reachable and entry.get('sig') and p not in pending:
            stl = with_export and target is not None and any(k in (load_facts(root, p, entry['sig']).get('probes') or {}) for k in ('stl_mesh', 'stl_walls'))
            if only is None or p in only:
                judge_part(root, reg, resolved, p, stage='design', intent=intent, target=target, reuse=reuse)
                if audit_run and not given_up(root, p, entry['sig']):
                    gate_report.baseline_add(root, audit_run, p, entry['sig'])
                if stl:
                    judge_part(root, reg, resolved, p, stage='export', intent=intent, target=target, reuse=reuse)
            if stl:                                  # judged now or in an earlier call of this plan: its verdict holds both stages
                stages = ('design', 'export')
        v = part_verdict(root, reg, p, stages=stages, defer_inherited=True)
        if not reachable:
            v['state'] = 'unreachable'
            if v['not_run']:
                v['verdict'] = 'unreachable'
        verdicts.append(v)
    return verdicts


def _finish(root, reg, resolved, run, args, parts, verdicts, plan, intent, unreachable_docs, more, note=None):
    """Write the report of this call, print its lines and return the exit code."""
    import gate_report
    layout = load_layout(root)
    lines = ['%s: document %r is not open' % (k, gate_common.document_entry(layout, k).get('name')) for k in unreachable_docs]
    report = gate_report.build(root, reg, resolved, run, 'run', 'design', verdicts, plan=plan, audit=args.audit, intent=intent, unreachable=lines)
    code = exit_code(verdicts, audit=args.audit, more=more)
    named_unreachable = [v['part'] for v in verdicts if v['verdict'] == 'unreachable' and v['part'] in (args.part or [])]
    nothing_reached = bool(verdicts) and all(v['verdict'] == 'unreachable' for v in verdicts)
    if code == EXIT_PASS and not args.audit and (named_unreachable or nothing_reached):
        code = EXIT_INFRA                        # what was asked for could not be looked at: never a pass
    report['exit'] = code
    if args.offline:
        report['offline'] = True
    gate_report.write(root, report)
    if args.audit:
        items = gate_report.write_proposal(root, report)
    print(gate_report.summary_line(report))
    if args.audit and not more:                   # while units are left, a part that was not judged yet is not in the baseline yet
        census = load_census(root)
        gone_over = [v['part'] for v in verdicts if v.get('inherited_part') is False and given_up(root, v['part'], (census['parts'].get(v['part']) or {}).get('sig'))]
        if gone_over:
            print('  not inherited (a unit was given up over its budget, so nothing judged its rules): %s. The audit has not looked at these parts in full: '
                  'their NOT_RUN rows count, and they stay "changed" until a run measures them' % gate_report._some(gone_over))
        later = [v['part'] for v in verdicts if v.get('inherited_part') is False and not v.get('proposed_part') and v['verdict'] != 'unreachable'
                 and v['part'] not in gone_over]
        if later:
            print('  not inherited (changed after the audit, or stamped): %s. Judged as in an ordinary run; nothing of them goes to the triage list'
                  % gate_report._some(later))
        new = [v['part'] for v in verdicts if v.get('proposed_part') and v['verdict'] != 'unreachable']
        if new:
            print('  not inherited (judged after the owner adopted his list): %s. Their rows block as in an ordinary run and are in the proposal; '
                  'only `gate.py owner adopt --quote "..."` makes them inherited' % gate_report._some(new))
    totals = report.get('totals') or {}
    if not more:
        # equal rows of a part are one line; what is measured and wrong comes first; the report holds every row
        opened = gate_report.group_rows([f for f in report['findings'] if f['state'] == 'open'])
        asked = gate_report.group_rows([f for f in report['findings'] if f['state'] == 'asked'])
        for line in gate_report.group_lines(opened, gate_report.RUN_LINES) + gate_report.group_lines(asked, gate_report.RUN_LINES // 2, word='ASK'):
            print(line)
        try:
            first = gate_report.first_export_parts(root, [v['part'] for v in verdicts if v.get('verdict') in ('pass', 'pass_attested')])
        except Exception:
            first = []
        if first:
            print(gate_report.first_export_line(first, rows=4))
    for line in lines:
        print('  UNREACHABLE  %s (its parts keep their stamps; nothing new was checked)' % line)
    for line in over_lines(report.get('over_budget') or []):     # a unit over its budget is a defect of a probe: never silent
        print('  ' + line)
    for line in unit_error_lines(plan):                          # why a unit gave no facts: the NOT_RUN row only says that it did not
        print('  ' + line)
    if note:
        print(note)
    if not verdicts and not more:
        print('nothing to check: no part changed since its stamp, or since the audit that judged it')
    by = totals.get('by_verdict') or {}
    if not more and totals.get('not_run') and not (by.get('FAIL') or by.get('BLOCKED') or by.get('GROW')):
        print('nothing failed: %d check(s) could not decide or did not run. They count as open and nothing has passed: do what each row says, then run again'
              % totals['not_run'])
    if totals.get('by_facts'):
        print('%d question(s) answered by the facts of the parts: the report lists each with its proof' % totals['by_facts'])
    if totals.get('waiting'):
        print('%d question(s) wait at inherited parts: asked when a part changes, and at its export' % totals['waiting'])
    print('report: gate/reports/%s.md' % run)
    if args.audit:
        groups = {gate_report.item_gid(i) for i in items}
        print('triage proposal: gate/proposals/triage.json (%d items in %d groups; `gate.py triage --md` shows it for the owner)' % (len(items), len(groups)))
    if code == EXIT_ATTEST:
        print('questions open: python3 gate/tools/gate.py attest list')
    if code == EXIT_INFRA:
        print('GATE: nothing was checked for %s: open the document and run again' % ', '.join(named_unreachable or [v['part'] for v in verdicts]))
    return code


def _fold_fits(root, layout, left):
    """Does the scan that confirms a run fit into the `left` seconds of this call: a pulse for every document whose last
    scan stored one, its last scan time (PACK_FACTOR and PACK_MARGIN_S as for a packed unit) for every other one. A
    document with neither is not known to be short: the confirming scan waits for the next call, as it did before."""
    if left < FOLD_MIN_S:
        return False
    docs = (load_census(root).get('docs') or {})
    need = 0.0
    for d in layout.get('documents') or []:
        e = docs.get(d.get('key')) or {}
        if e.get('open') is False:
            continue
        if isinstance(e.get('pulse'), dict):
            need += PULSE_MIN_S
            continue
        cost = e.get('scan_s')
        if not isinstance(cost, (int, float)) or isinstance(cost, bool):
            return False
        need += PACK_FACTOR * float(cost) + PACK_MARGIN_S
    return left >= max(FOLD_MIN_S, need)


def cmd_run(root, args):
    """gate.py run [--changed | --all | --part ID ...] [--parts ID,ID] [--for KEY] [--wall 20] [--audit] [--offline].

    args.changed (the default when nothing is named): parts whose census signature has no valid stamp (an inherited
        part the audit judged, and that has not changed since, is left out: _settled), and every part with a unit whose
        stored facts no longer hold (stale_units). Only the stale units are measured (new_plan with `stale`): a part
        checked at its present geometry, whose neighbours did not change, costs no call, stamp or no stamp
    args.fresh: every unit of the chosen parts is measured again
    args.all: every part of the census        args.part: these parts
    args.intent: the parts this change was meant to touch (regression.unintended judges the rest)
    args.wall: do not start a unit when less than its budget is left of this many seconds
    args.audit: findings are recorded as inherited (they go to the triage list), nothing blocks: exit 0 unless 3 or 4.
        Each part an audit judges is noted in the baseline of inherited parts with its signature (once: a later audit
        does not move it). Such a part is not asked its standing questions until it changes or is exported
    args.offline: no Fusion call; stored facts are judged again with today's parameters and judges
    args.target (--for KEY): the .3mf project whose declared target the parts are judged for. A judgement holds for one
        target (its layer height, the tags of its fits), so a project that declares several must name one: a run
        without it is refused (exit 4) and never judges against "no target". `gate.py export --for KEY` judges the
        stored facts again for its own target, so one run serves the export for every project

    Continues the current plan when one is unfinished and covers the same parts, else makes a new one. After the units:
    judges every part (judge_part), writes the report (gate_report.build / write), prints its summary.
    Returns 0 pass, 1 open block findings, 2 attest questions open, 3 more calls needed, 4 infrastructure.

    A new plan starts with a scan (unless args.no_scan or args.offline), so the parts and their signatures come from
    the open documents. After EVERY call the parts whose units are all done are judged and the report is written, so
    `gate.py status` is current while a run is still going; the rows of a part add up over the calls.
    A part whose STL was measured at its present signature (an export that was refused) is judged at the export stage
    too, with and without --offline: a run never reads as a pass while `gate.py status` shows an open wall or mesh row.
    A named part whose document is not open, or a run that reached no part at all, is exit 4."""
    root = os.path.abspath(root)
    reg = gate_registry.load_default(root)
    project, layout = load_project(root), load_layout(root)
    target_key = getattr(args, 'target', None)
    target = gate_common.target_of(project, target_key)
    if target is None and project.get('targets'):
        raise GateError('gate/project.json declares several targets, and a part is judged for one of them (layer height, measured fits): '
                        'name the .3mf project with --for KEY (%s)' % ', '.join(sorted(project['targets'])))
    resolved = gate_params.resolve(reg, gate_params.load_overrides(root), target)
    request, wall = _request(args), float(args.wall)
    last = load_plan(root)
    intent = args.intent if args.intent is not None else ((last or {}).get('intent') if (last or {}).get('command') == 'run' else None)

    if args.offline:
        census = load_census(root)
        parts = _select(root, args, census, intent)
        run = gate_common.new_run_id()
        verdicts = _judge_all(root, reg, resolved, parts, census, intent, target, [], with_export=True, audit_run=run if args.audit else None)
        return _finish(root, reg, resolved, run, args, parts, verdicts, None, intent, [], False,
                       note='offline: stored facts judged again with today\'s parameters and judges; nothing was asked of Fusion')

    budget = gate_fusion_rpc.budget(layout)
    if wall + WALL_GRACE_S < budget and not getattr(args, 'folded', False):
        raise GateError('--wall %g is shorter than the budget of one unit (%g s, gate/layout.json "fusion"."budget_s"): no unit could start' % (wall, budget))
    t_end = _clock() + wall
    plan = last if (last and last.get('command') == 'run' and last.get('request') == request and progress(last)[0] < progress(last)[1]) else None
    planned_now = plan is None
    # the call after a plan whose units were measured over several calls: it scans again and measures what changed in
    # Fusion meanwhile. Its own plan takes the stored facts over, also for --fresh (they were just measured)
    confirming = bool(last and last.get('command') == 'run' and last.get('request') == request and last.get('confirm'))
    fresh = getattr(args, 'fresh', False) and not confirming
    if plan is None:
        unreachable = [k for k, d in load_census(root)['docs'].items() if d.get('open') is False]
        if not getattr(args, 'no_scan', False):
            res = scan(root, wall_s=wall, use_pulse=not fresh)
            _print_scan(res, _hint_faces(root))
            if res['errors'] or res['clashes']:
                raise GateError('the scan did not hold: %s' % '; '.join(['%s: %s' % kv for kv in sorted(res['errors'].items())] + res['clashes']))
            if not res['done']:
                print('scan: more documents to read before the units can be planned')
                print('NEXT %d/%d' % res['progress'])
                return EXIT_MORE
            unreachable = res['unreachable']
        census = load_census(root)
        if not census['parts']:
            closed = [k for k in unreachable if any(d.get('key') == k for d in layout['documents'])]
            if closed:
                raise GateError('gate/census.json holds no part yet, and the registered document%s %s %s not open in Fusion: open %s and run again'
                                % ('s' if len(closed) > 1 else '', ', '.join('"%s"' % gate_common.document_entry(layout, k).get('name') for k in closed),
                                   'are' if len(closed) > 1 else 'is', 'them' if len(closed) > 1 else 'it'))
            raise GateError('gate/census.json holds no part: register the document in gate/layout.json and run gate.py scan')
        shadow = {}
        stale = stale_units(root, reg, resolved, census, target_key=target_key, layout=layout, project=project, shadow=shadow)
        parts = _select(root, args, census, args.intent, stale=stale, reg=reg, resolved=resolved)
        intent = args.intent
        only = only_probes(reg, getattr(args, 'only', None))
        plan = new_plan(root, reg, parts, 'run', intent, args.audit, stale=None if fresh else stale, shadow=None if fresh else shadow, only=only or None)
        plan['request'], plan['fresh'] = request, bool(fresh)
        if only:
            print('partial run: only %s is measured; the other rules of the part stay NOT_RUN until a full `gate.py run`' % ', '.join(sorted(only)))
        for key in unreachable:
            _skip_doc(plan, key)
        save_plan(root, plan)
    parts = plan['parts']
    before = {u['id']: u['state'] for u in plan['units']}
    res = run_units(root, reg, resolved, plan, wall_s=t_end - _clock(), target_key=target_key)
    confirm = None
    if not res['more'] and not planned_now and not getattr(args, 'no_scan', False) and not res['changed_under_run']:
        # the units of this plan were measured over several calls, the last scan is as old as the plan: a neighbour
        # moved meanwhile would be measured on the model before the move. Never exit 0 on that: the next call scans
        # once more, and its plan measures what changed (one short call when nothing did)
        plan['confirm'] = True
        save_plan(root, plan)
        confirm = ('the units are done; they were measured over several calls, so the next call scans once more and measures again what '
                   'changed in Fusion while they ran (nothing, usually: then it sends no unit)')
    census = load_census(root)
    pending = {u['part'] for u in plan['units'] if not _finished(u)}
    # judged: every part at the first call of a plan, then only the parts whose units finished in this call (a judge
    # whose facts, parameters and ctx are what it judged before is not run again either: judge_part)
    touched = None if not plan.get('judged') else {u['part'] for u in plan['units'] if _finished(u) and before.get(u['id']) != u['state']}
    verdicts = _judge_all(root, reg, resolved, parts, census, intent, target, res['unreachable'], with_export=True, pending=pending,
                          audit_run=plan['run'] if args.audit else None, only=touched, reuse=not plan.get('fresh'))
    if not plan.get('judged'):
        plan['judged'] = True
        save_plan(root, plan)
    rescan = (bool(res['changed_under_run']) or confirm is not None) and not res['more']
    if rescan and not getattr(args, 'folded', False) and _fold_fits(root, layout, t_end - _clock()):
        # the scan that confirms the run fits into this call: it is made now (a pulse, usually, when nothing changed),
        # and its plan measures what changed meanwhile. The judgement and the report come from that plan
        if res['changed_under_run']:
            print('changed in Fusion while the run was going: %s. Their units were skipped; this call scans again.' % ', '.join(res['changed_under_run']))
        import argparse
        inner = argparse.Namespace(**vars(args))
        inner.wall, inner.folded = t_end - _clock(), True
        return cmd_run(root, inner)
    note = ('changed in Fusion while the run was going: %s. Their units were skipped; the next call scans again.'
            % ', '.join(res['changed_under_run'])) if res['changed_under_run'] else confirm
    shadowed = shadow_line(plan) if not res['more'] else None
    if shadowed:
        note = shadowed if note is None else '%s\n%s' % (note, shadowed)
    code = _finish(root, reg, resolved, plan['run'], args, parts, verdicts, plan, intent, res['unreachable'], res['more'] or rescan, note)
    if res['more']:
        print('NEXT %d/%d' % (res['done'], res['total']))
    elif rescan:
        print('NEXT 0/1')
    return code
