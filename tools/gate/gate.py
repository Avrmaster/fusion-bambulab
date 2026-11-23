#!/usr/bin/env python3
"""The one command of the gate. Standard library only.

    python3 gate/tools/gate.py doctor                      vendored copy vs LOCK, interpreter, Fusion reachable, documents open
    python3 gate/tools/gate.py rules [--md] [--category C] effective rules with this project's numbers
    python3 gate/tools/gate.py params check | explain NAME [--target KEY]
    python3 gate/tools/gate.py scan [--doc KEY]            discover parts and signatures (short Fusion calls), update census.json
    python3 gate/tools/gate.py run [--changed | --all | --part ID ...] [--parts ID,ID] [--for KEY] [--wall 120] [--audit] [--offline] [--no-scan]
    python3 gate/tools/gate.py status [--json] [--digest]  no Fusion
    python3 gate/tools/gate.py attest list [--part ID] [--waiting] | answer Q (--label "..." | --na "why" | --param NAME | --shot PATH) [--for KEY]
    python3 gate/tools/gate.py export PART [...] [--for KEY] [--wall 120]   quarantine STL -> STL checks -> promote + stamp
    python3 gate/tools/gate.py export --generated STL [--for KEY]  a part made by code: STL checks + its attest, then stamp
    python3 gate/tools/gate.py verify [PROJECT.3mf]        stamps <-> STLs <-> meshes, project consistency; no Fusion
    python3 gate/tools/gate.py slice [PROJECT] [--changed | --all | --plates 1,4]   the slicer's support per object, on a copy
    python3 gate/tools/gate.py report [--last]
    python3 gate/tools/gate.py triage [--md]               inherited findings grouped for the owner
    python3 gate/tools/gate.py owner waive F-ID|Q-ID --reason "..." --quote "..."
    python3 gate/tools/gate.py owner adopt --quote "..."     the audit becomes the list of inherited findings and parts
    python3 gate/tools/gate.py owner decide T-ID|G-ID fix|waive|tolerate|rule_wrong --quote "..." [--note "..."] [--part ID]
    python3 gate/tools/gate.py owner param NAME VALUE --tag M|U|D|S --source "..." [--key SIZE] [--under KEY] --quote "..."
    python3 gate/tools/gate.py owner bundle PROPOSAL [--dry-run] --quote "..."   a prepared proposal of param, target, waiver,
                                                           triage and mode entries, applied in one call or not at all
    python3 gate/tools/gate.py owner baseline PROJECT "OBJECT" [...] --quote "..." [--reason "..."]   support baseline from the last slice
    python3 gate/tools/gate.py owner outside PROJECT "OBJECT" [...] --reason "..." [--dry-run | --quote "..."]   objects of a
                                                           .3mf project that no registered document makes: they pass a save
                                                           only while their mesh is the one in the file at this call

`--for KEY` names the .3mf project (its key in gate/layout.json) whose declared target a part is judged for. A project
with one declared target needs none; with several, `run` and `export` refuse to guess.

A vendored copy that was changed by hand (gate/LOCK.json says "forked") stops every command that judges, stamps or
writes: run, scan, export, verify, attest answer, owner. doctor, rules, params, status, report, triage and attest list
still read.

Exit codes of every sub-command: 0 pass; 1 open block findings; 2 attest questions open; 3 more calls needed (the last
line printed is `NEXT n/m`); 4 infrastructure (no project, a broken file, a parameter error, Fusion not answering, a
part of the gate that is not built yet).

This file only parses the command line and hands over: COMMANDS names, per sub-command, the module and the function
`cmd_<name>(root, args) -> exit code` that does the work. `root` is the project root (the folder with gate/project.json),
`args` the argparse namespace described at each parser below.

`run` is resumable: it works for `--wall` seconds, never starts a unit it has no time for, and exits 3 with `NEXT n/m`
as its last line until every unit is done. A new run scans the open documents first (`--no-scan`: take the census as it
is). `owner ...` is the only writer of gate/owner/*: a project's permission rule asks the owner before each such call, and from
an agent session it needs the one-time ticket the PreToolUse hook leaves when --quote is the owner's own words (_take_ticket)."""
import argparse, contextlib, copy, datetime, difflib, hashlib, importlib, io, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_common                                                  # noqa: E402
from gate_common import GateError, EXIT_INFRA, EXIT_MORE, EXIT_PASS, find_root          # noqa: E402

COMMANDS = {                                   # sub-command -> (module, function)
    'doctor': ('gate_vendor', 'cmd_doctor'),
    'rules': ('gate_render', 'cmd_rules'),
    'params': ('gate_params', 'cmd_params'),
    'scan': ('gate_plan', 'cmd_scan'),
    'run': ('gate_plan', 'cmd_run'),
    'status': ('gate_report', 'cmd_status'),
    'attest': ('gate_attest', 'cmd_attest'),
    'export': ('gate_stamp', 'cmd_export'),
    'verify': ('gate_guard', 'cmd_verify'),
    'slice': ('gate_slice', 'cmd_slice'),
    'coupons': ('gate_coupons', 'cmd_coupons'),
    'report': ('gate_report', 'cmd_report'),
    'triage': ('gate_report', 'cmd_triage'),
    'owner': ('gate', 'cmd_owner'),
}
NO_ROOT = ('doctor', 'rules')                  # these also work in a folder that is not gated (root is then None)
# commands that judge, stamp or write: they do not run on a vendored copy that was changed by hand (gate/LOCK.json)
NEEDS_INTACT = ('run', 'scan', 'export', 'verify', 'owner', 'slice')
# commands after which the digest the SessionStart hook prints is built again (gate_report.refresh_digest): those that
# can change the state of the gate. `status` builds it itself; a `run` or `scan` that needs more calls (exit 3) does
# not, it would add a judgement of every part to every short call: the hook then says the digest is stale.
REFRESH_DIGEST = ('scan', 'run', 'attest', 'export', 'verify', 'triage', 'owner', 'params', 'slice')
DECISIONS = ('fix', 'waive', 'tolerate', 'rule_wrong')


class _Parser(argparse.ArgumentParser):
    def __init__(self, *a, **kw):
        kw.setdefault('allow_abbrev', False)  # "--quo" is no --quote: what the hook checked is what is applied
        argparse.ArgumentParser.__init__(self, *a, **kw)

    def error(self, message):                  # argparse would exit 2, which here means "attest questions open"
        self.print_usage(sys.stderr)
        sys.stderr.write('%s: %s\n' % (self.prog, message))
        sys.exit(EXIT_INFRA)


def parser():
    """The argparse parser. The attributes of `args` each cmd_ function gets:

    all       command, root (the --root option or None)
    rules     md (bool), category (name or None)
    params    action ("check" | "explain"), name (for explain, else None), target (.3mf project key or None)
    scan      doc (document key or None = every registered document)
    run       changed (bool), all (bool), part (list of part ids, [] when none), intent (list of part ids from --parts,
              None when not given), wall (seconds, float), audit (bool), offline (bool), no_scan (bool),
              target (--for KEY or None), fresh (bool: no stored facts are taken over)
    status    json (bool), digest (bool)
    attest    action ("list" | "answer"), question (Q id or None), label, na, param, shot (each text or None),
              target (--for KEY or None), part (list: the questions of one part, waiting ones too), waiting (bool)
    export    parts (list of part ids), generated (STL path or None), target (--for KEY or None), wall (seconds)
    verify    project (path of a .3mf or None = every registered project)
    slice     project (key or path, None = every registered project), changed, all (bool), plates (text or None),
              keep (bool), timeout (seconds)
    report    last (bool)
    triage    md (bool)
    owner     action ("waive" | "decide" | "adopt" | "param" | "bundle" | "baseline" | "table" | "outside"); outside: project
              (key), objects (list), reason, quote (None with --dry-run), dry_run (bool); waive: finding, reason, quote; decide: item (T-id
              of a row or G-id of a group), decision, quote, note, part (with a G-id: only the rows of this part); adopt:
              quote; param: name, value (text as typed), tag, source, key, quote; bundle: proposal (path), dry_run (bool),
              quote (None with --dry-run); baseline: project, objects (list), quote, reason"""
    p = _Parser(prog='gate.py', description='One gate for printed parts: rules, checks, stamps.')
    p.add_argument('--root', help='project root (default: the nearest folder above the working directory with gate/project.json)')
    sub = p.add_subparsers(dest='command', parser_class=_Parser)
    sub.required = True
    sub.add_parser('doctor')
    s = sub.add_parser('rules'); s.add_argument('--md', action='store_true'); s.add_argument('--category')
    s = sub.add_parser('params'); s.add_argument('action', choices=('check', 'explain')); s.add_argument('name', nargs='?')
    s.add_argument('--target')
    s = sub.add_parser('scan'); s.add_argument('--doc')
    s = sub.add_parser('run')
    g = s.add_mutually_exclusive_group()
    g.add_argument('--changed', action='store_true'); g.add_argument('--all', action='store_true')
    s.add_argument('--part', action='append', default=[]); s.add_argument('--parts', dest='intent', type=lambda t: [x for x in t.split(',') if x])
    s.add_argument('--wall', type=float, default=120.0); s.add_argument('--audit', action='store_true'); s.add_argument('--offline', action='store_true')
    s.add_argument('--no-scan', dest='no_scan', action='store_true'); s.add_argument('--for', dest='target')
    s.add_argument('--fresh', action='store_true', help='measure every unit of the chosen parts again, also when its stored facts still hold')
    s.add_argument('--only', type=lambda t: [x for x in t.split(',') if x],
                   help='measure only these probes (comma list: inserts,thin or fusion_inserts,fusion_thin): a fast check of one kind of change; the other rules of the part stay NOT_RUN until a full run')
    s = sub.add_parser('status'); s.add_argument('--json', action='store_true'); s.add_argument('--digest', action='store_true')
    s = sub.add_parser('attest'); s.add_argument('action', choices=('list', 'answer')); s.add_argument('question', nargs='?')
    g = s.add_mutually_exclusive_group()
    g.add_argument('--label'); g.add_argument('--na'); g.add_argument('--param'); g.add_argument('--shot')
    s.add_argument('--for', dest='target'); s.add_argument('--part'); s.add_argument('--waiting', action='store_true')
    s = sub.add_parser('export'); s.add_argument('parts', nargs='*'); s.add_argument('--generated'); s.add_argument('--for', dest='target')
    s.add_argument('--wall', type=float, default=120.0)
    s = sub.add_parser('verify'); s.add_argument('project', nargs='?')
    s = sub.add_parser('slice'); s.add_argument('project', nargs='?')
    g = s.add_mutually_exclusive_group()
    g.add_argument('--changed', action='store_true'); g.add_argument('--all', action='store_true'); g.add_argument('--plates')
    s.add_argument('--keep', action='store_true'); s.add_argument('--timeout', type=float, default=1800.0)
    s = sub.add_parser('coupons'); s.add_argument('--for', dest='target', metavar='KEY')
    s = sub.add_parser('report'); s.add_argument('--last', action='store_true')
    s = sub.add_parser('triage'); s.add_argument('--md', action='store_true')
    s = sub.add_parser('owner')
    osub = s.add_subparsers(dest='action', parser_class=_Parser)
    osub.required = True
    o = osub.add_parser('waive'); o.add_argument('finding'); o.add_argument('--reason', required=True); o.add_argument('--quote', required=True)
    o = osub.add_parser('decide'); o.add_argument('item'); o.add_argument('decision', choices=DECISIONS)
    o.add_argument('--quote', required=True); o.add_argument('--note'); o.add_argument('--part')
    o = osub.add_parser('adopt'); o.add_argument('--quote', required=True)
    o = osub.add_parser('param'); o.add_argument('name'); o.add_argument('value'); o.add_argument('--tag', required=True, choices=('M', 'U', 'D', 'S'))
    o.add_argument('--source', required=True); o.add_argument('--key'); o.add_argument('--quote', required=True)
    o.add_argument('--under', metavar='KEY', help='with --tag M on a fit parameter: the .3mf project the value was printed from; its declared target is stored as "measured_under"')
    o = osub.add_parser('bundle'); o.add_argument('proposal'); o.add_argument('--quote'); o.add_argument('--dry-run', dest='dry_run', action='store_true')
    o = osub.add_parser('baseline'); o.add_argument('project'); o.add_argument('objects', nargs='+'); o.add_argument('--quote', required=True); o.add_argument('--reason')
    o = osub.add_parser('outside'); o.add_argument('project'); o.add_argument('objects', nargs='+'); o.add_argument('--reason', required=True)
    o.add_argument('--quote'); o.add_argument('--dry-run', dest='dry_run', action='store_true')
    o = osub.add_parser('table'); o.add_argument('table', choices=TABLE_ROWS); o.add_argument('row', nargs='?')
    o.add_argument('--value', help='bearings: "d,D,B" in mm; pins, screws, screws_set: the stock lengths "16,20,25"')
    o.add_argument('--under', metavar='KEY', help='inserts, seat_fit: the .3mf project the print was made from (its declared target is recorded)')
    o.add_argument('--source', required=True); o.add_argument('--quote'); o.add_argument('--dry-run', dest='dry_run', action='store_true')
    return p


def _owner_file(root, name):
    return os.path.join(root, 'gate', 'owner', name)


def _open_rows(root, reg):
    """Every finding and every question that is open now, by id: the latest report first, then the stored judgements."""
    import gate_plan, gate_report
    rows = {}
    latest = gate_report.load_latest(root) or {}
    for f in latest.get('findings') or []:
        if f.get('state') in ('open', 'asked', 'inherited'):
            rows.setdefault(f.get('id'), f)
    for q in latest.get('questions') or []:
        if not q.get('answer'):
            rows.setdefault(q.get('q'), q)
    census = gate_common.load_census(root)
    for pid in sorted(census['parts']):
        e = census['parts'][pid]
        if e.get('gone') or not e.get('sig') or not os.path.isfile(gate_common.facts_path(root, pid, e['sig'])):
            continue
        v = gate_plan.part_verdict(root, reg, pid, stages=('design', 'export'))
        for f in v['open'] + v['asked'] + v['inherited']:
            rows[f['id']] = f                    # the stored judgement of the present geometry wins over an older report
        for q in v['questions'] + v['ask_questions']:
            rows[q['q']] = q
    return rows


def _owner_waive(root, args):
    import gate_features, gate_registry
    reg = gate_registry.load_default(root)
    row = _open_rows(root, reg).get(args.finding)
    if row is None:
        raise GateError('%s is not an open finding or question of the present geometry (gate.py status and gate.py attest list show them)' % args.finding)
    is_question = 'q' in row and 'evidence' in row
    if is_question:                              # a question: the waiver answers it
        if 'waiver' not in row.get('evidence', []):
            raise GateError('question %s (%s) takes %s as evidence, not a waiver' % (row['q'], row['rule'], ' or '.join(row.get('evidence') or ['nothing'])))
        text = row.get('question')
    else:
        text = row.get('text')
    path = _owner_file(root, 'waivers.json')
    data = gate_common.read_json(path, {})
    if not isinstance(data, dict):
        raise GateError('gate/owner/waivers.json must be an object with "waivers"')
    waivers = list(data.get('waivers') or [])
    n = 0
    for w in waivers:
        try:
            n = max(n, int(str(w.get('id', 'W-0')).split('-')[1]))
        except (AttributeError, IndexError, ValueError):
            pass
    waiver = {'id': 'W-%d' % (n + 1), 'rule': row['rule'], 'part': row['part'], 'anchor': row.get('anchor') or gate_features.part_anchor(),
              'context': row.get('context'), 'reason': args.reason, 'quote': args.quote, 'date': datetime.date.today().isoformat(),
              'for': args.finding, 'text': text}
    errors = gate_features.validate_waiver(waiver)
    if errors:
        raise GateError('waiver refused, nothing written:\n  ' + '\n  '.join(errors))
    for w in waivers:
        if (isinstance(w, dict) and w.get('rule') == waiver['rule'] and w.get('part') == waiver['part'] and w.get('context') == waiver['context']
                and gate_features.anchors_match(w.get('anchor'), waiver['anchor']) and not gate_features.validate_waiver(w)):
            raise GateError('%s is already waived by %s' % (args.finding, w.get('id')))
    data['waivers'] = waivers + [waiver]
    gate_common.write_json(path, data)
    print('%s written to gate/owner/waivers.json: %s on %s, %s' % (waiver['id'], waiver['rule'], waiver['part'], text))
    if is_question:                              # the waiver is the evidence: store it as the answer of that question
        import gate_attest, gate_params
        if any(q['q'] == row['q'] for q in gate_attest.open_questions(root, reg, [row['part']], waiting=True)):
            resolved = gate_params.resolve(reg, gate_params.load_overrides(root), gate_common.target_of(gate_common.load_project(root)))
            gate_attest.answer(root, reg, resolved, row['q'], 'waiver', waiver['id'])
            print('question %s is answered by it' % row['q'])
        else:                                    # the question came from a finding of the same rule: the waiver closed that finding
            print('the finding behind question %s is waived by it' % row['q'])
    print('it holds while this feature stays as it is; a change of the feature opens the finding again')
    return EXIT_PASS


def _adopt(root, data):
    """Take over into the owner's triage list what the proposal of the audit holds and the list does not: items (by
    finding id; an item the owner has keeps its decision) and baseline entries of parts (the geometry the audit judged).
    Returns (the list, items added, parts added). GateError when there is no proposal."""
    import gate_report
    prop = gate_common.read_json(os.path.join(root, 'gate', 'proposals', 'triage.json'), {})
    src = prop.get('data') if isinstance(prop, dict) else None
    if not isinstance(src, dict) or not (src.get('items') or src.get('baseline')):
        raise GateError('there is no triage list: `gate.py run --audit` makes the proposal first')
    data = dict(data) if isinstance(data, dict) else {}
    if not data.get('adopted'):
        data['adopted'] = dict(src.get('adopted') or {}, adopted_at=gate_common.now())
    items = [dict(i) for i in data.get('items') or [] if isinstance(i, dict)]
    have, ids = {i.get('finding') for i in items}, {i.get('id') for i in items}
    n = 0
    for i in items:
        try:
            n = max(n, int(str(i.get('id', 'T-0')).split('-')[1]))
        except (IndexError, ValueError):
            pass
    added = 0
    for i in src.get('items') or []:
        if not isinstance(i, dict) or i.get('finding') in have:
            continue
        i = dict(i)
        if i.get('id') in ids:                       # a number the owner's list already uses: the new row gets the next one
            n += 1
            i['id'] = 'T-%04d' % n
        ids.add(i['id']); have.add(i.get('finding'))
        try:
            n = max(n, int(str(i['id']).split('-')[1]))
        except (IndexError, ValueError):
            pass
        items.append(i)
        added += 1
    base = dict(data.get('baseline') or {})
    fresh = {p: dict(e, adopted_at=gate_common.now()) for p, e in (src.get('baseline') or {}).items() if p not in base and isinstance(e, dict)}
    base.update(fresh)
    data['items'], data['baseline'] = items, base
    return data, added, len(fresh)


def _owner_adopt(root, args):
    """The owner takes the audit as the starting state of the project: its findings become the list of inherited
    findings, its parts the inherited parts. Run again after a later audit, it takes over what that one added."""
    path = _owner_file(root, 'triage.json')
    data = gate_common.read_json(path, {})
    if not isinstance(data, dict):
        raise GateError('gate/owner/triage.json must be an object with "items"')
    data, added, parts = _adopt(root, data)
    data.setdefault('quotes', []).append({'quote': args.quote, 'date': datetime.date.today().isoformat(), 'items': added, 'parts': parts})
    gate_common.write_json(path, data)
    print('adopted the audit %s: %d row(s) and %d part(s) added; the list now holds %d inherited findings on %d inherited parts'
          % ((data.get('adopted') or {}).get('run'), added, parts, len(data['items']), len(data['baseline'])))
    print('%d row(s) not decided (`gate.py triage --md` shows them by group)' % sum(1 for i in data['items'] if not i.get('decision')))
    return EXIT_PASS


def _owner_decide(root, args):
    import gate_report
    if not (args.quote or '').strip():
        raise GateError('--quote must hold the owner\'s own words')
    path = _owner_file(root, 'triage.json')
    data = gate_common.read_json(path, {})
    if not isinstance(data, dict):
        raise GateError('gate/owner/triage.json must be an object with "items"')
    adopted_now = False
    if not data.get('items'):                    # the first decision adopts the proposal of the audit as the inherited list
        data, added, parts = _adopt(root, data)
        if not data.get('items'):
            raise GateError('the audit found no row to decide: `gate.py owner adopt --quote "..."` takes its parts as inherited')
        adopted_now = True
    today = datetime.date.today().isoformat()
    if str(args.item).startswith('G-'):          # a group: every row of that kind (rule + cause) that has no decision yet
        only = getattr(args, 'part', None)
        rows = [i for i in data['items'] if isinstance(i, dict) and gate_report.item_gid(i) == args.item and (not only or i.get('part') == only)]
        if not rows:
            raise GateError('no row of the group %s%s in the triage list (`gate.py triage` lists the groups)' % (args.item, (' on %s' % only) if only else ''))
        todo = [i for i in rows if not i.get('decision')]
        if not todo:
            raise GateError('every row of the group %s is decided already; change one row with its T-id' % args.item)
        for i in todo:
            i.update(decision=args.decision, quote=args.quote, note=args.note, date=today)
        gate_common.write_json(path, data)
        if adopted_now:
            print('adopted the audit %s as the list of inherited findings: %d rows' % ((data.get('adopted') or {}).get('run'), len(data['items'])))
        print('%s = %s: %d row(s) of %s on %s (%d of the group were decided before)'
              % (args.item, args.decision, len(todo), rows[0].get('rule'), ', '.join(sorted({i.get('part') or '' for i in todo})), len(rows) - len(todo)))
        print('%d row(s) not decided' % sum(1 for i in data['items'] if not i.get('decision')))
        return EXIT_PASS
    item = next((i for i in data['items'] if isinstance(i, dict) and i.get('id') == args.item), None)
    if item is None:
        later = any(i.get('id') == args.item for i in gate_report.not_adopted(root)[0])
        raise GateError('no item %s in the triage list (%s)' % (args.item, 'it is in the proposal of a later audit: `gate.py owner adopt --quote "..."` takes it over first'
                                                                 if later else 'a finding that was not in the audit is an ordinary block, not triage'))
    item.update(decision=args.decision, quote=args.quote, note=args.note, date=today)
    gate_common.write_json(path, data)
    if adopted_now:
        print('adopted the audit %s as the list of inherited findings: %d rows' % ((data.get('adopted') or {}).get('run'), len(data['items'])))
    print('%s = %s (%s on %s)' % (item['id'], args.decision, item.get('rule'), item.get('part')))
    if args.decision == 'waive':
        print('the part can be exported with this decision. To have it on record as a waiver with its reason (it then holds until the feature changes): '
              'python3 gate/tools/gate.py owner waive %s --reason "..." --quote "..."' % item.get('finding'))
    left = sum(1 for i in data['items'] if not i.get('decision'))
    print('%d row(s) not decided' % left)
    return EXIT_PASS


def _owner_param(root, args):
    import gate_params, gate_registry
    reg = gate_registry.load_default(root)
    if args.name not in reg['params']:
        gate_params.explain(reg, args.name)      # raises ParamError with the nearest names
    definition = reg['params'][args.name]
    value = args.value
    if isinstance(definition.get('default'), (int, float)) or definition.get('formula') or args.key:
        try:
            value = float(args.value)
        except ValueError:
            raise GateError('%s takes a number, got %r' % (args.name, args.value))
        if value == int(value) and isinstance(definition.get('default'), int) and not isinstance(definition.get('default'), bool):
            value = int(value)
    path = _owner_file(root, 'params.json')
    raw = gate_common.read_json(path, {})
    if not isinstance(raw, dict):
        raise GateError('gate/owner/params.json must be an object')
    today = datetime.date.today().isoformat()
    entry = dict(raw.get(args.name) or {}) if isinstance(raw.get(args.name), dict) else {}
    if args.key:
        if args.tag == 'M' and definition.get('class') == 'fit':
            # one entry has one tag and one "measured_under": M for one size would make the base value, and every other
            # size, measured for this project too. So the base value must be measured HERE already.
            project_ = gate_common.load_project(root)
            targets_ = project_.get('targets') or {}
            key_ = getattr(args, 'under', None) or (list(targets_)[0] if len(targets_) == 1 else None)
            try:
                here = gate_params.resolve(reg, {k: v for k, v in raw.items() if not k.startswith('_')}, targets_.get(key_))[args.name]
                base_tag, why = here['tag'], here.get('tag_why')
            except GateError:
                base_tag, why = 'D', None
            if base_tag != 'M':
                raise GateError('%s[%s]: the tag belongs to the whole parameter, and its base value is %s (%s) for this project%s. Storing this size '
                                'as M would mark every size of %s as printed here. Record it as the owner\'s decision (--tag U), or measure and '
                                'store the base value first (without --key); nothing was written'
                                % (args.name, args.key, base_tag, gate_params.TAG_WORDS.get(base_tag, base_tag), ': ' + why if why else '', args.name))
        if not entry:
            entry = {'value': definition.get('default')}
        entry['per'] = dict(entry.get('per') or {}, **{args.key: value})
    else:
        entry['value'] = value
    entry.update(tag=args.tag, source=args.source, date=today)
    if args.quote:
        entry['quote'] = args.quote
    project = gate_common.load_project(root)
    entry.pop('measured_under', None)
    if args.tag == 'M' and definition.get('class') == 'fit' and not definition.get('formula'):
        # a fit is measured only where it was printed: the value is stored with the nozzle, material, hole compensation
        # and layer height of the project it was printed from
        targets = project.get('targets') or {}
        key = getattr(args, 'under', None) or (list(targets)[0] if len(targets) == 1 else None)
        if key is None or key not in targets:
            raise GateError('%s is a fit: a value measured on a print is stored with what it was printed under. Name the .3mf project it was '
                            'printed from with --under KEY (declared targets: %s); nothing was written' % (args.name, ', '.join(sorted(targets)) or 'none'))
        under = {k: targets[key].get(k) for k in gate_params.MEASURED_UNDER}
        missing = [k for k in gate_params.MEASURED_UNDER if under[k] is None]
        if missing:
            raise GateError('the declared target of %s does not say %s: the owner adds what the project prints with to gate/project.json "targets" '
                            'first; nothing was written' % (key, ', '.join(missing)))
        why = _printed_as_declared(root, key, targets[key])
        if why:
            raise GateError('%s is a fit measured on a print, and it is stored with what the project %s prints with. %s. Nothing was written: '
                            'put the project file and its declared target in step first (gate.py verify), then record the value'
                            % (args.name, key, why))
        entry['measured_under'] = under
    elif getattr(args, 'under', None):
        raise GateError('--under goes with --tag M on a fit parameter (%s is %s, tag %s); nothing was written' % (args.name, definition.get('class'), args.tag))
    raw = dict(raw, **{args.name: entry})
    overrides = {k: v for k, v in raw.items() if not k.startswith('_')}
    errors = gate_params.check(reg, overrides, gate_common.target_of(project))
    if errors:
        raise GateError('parameter refused, nothing written:\n  ' + '\n  '.join(errors))
    gate_common.write_json(path, raw)
    resolved = gate_params.resolve(reg, overrides, gate_common.target_of(project))
    print('%s = %s' % (args.name if not args.key else '%s[%s]' % (args.name, args.key), gate_params.limit_text(resolved[args.name], args.key)))
    if entry.get('measured_under'):
        print('measured under: %s' % ', '.join('%s %s' % (k, entry['measured_under'][k]) for k in gate_params.MEASURED_UNDER))
    print('stored judgements were made with the old value: python3 gate/tools/gate.py run --offline judges them again')
    return EXIT_PASS


def _printed_as_declared(root, key, target):
    """None when the .3mf project `key` holds, today, the hole compensation, layer height and nozzle its declared target
    says; else why not, as one sentence. A coupon is printed from the file, not from the declaration: while the two
    differ (the state the settings rule exists for), a value measured on that print must not be stored as measured
    under the declared values."""
    from probes import project_consistency
    entry = next((e for e in gate_common.load_layout(root).get('projects') or [] if isinstance(e, dict) and e.get('key') == key), None)
    if entry is None or not entry.get('file'):
        return 'gate/layout.json registers no .3mf project with the key %s, so nothing says what the coupon was printed with' % key
    path = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, *str(entry['file']).split('/'))
    if not os.path.isfile(path):
        return 'the project file %s does not exist, so nothing says what the coupon was printed with' % entry['file']
    keys = ('nozzle_diameter', 'xy_hole_compensation', 'layer_height')
    try:
        got = project_consistency.read_fit_settings(path, keys)
    except GateError as e:
        return 'the project file %s cannot be read (%s)' % (entry['file'], e)
    glob = got.get('global') or {}
    off = []
    for k in keys:
        want, have = target.get(project_consistency.target_key(k)), glob.get(k)
        if not (isinstance(have, (int, float)) and isinstance(want, (int, float)) and abs(have - want) <= 5e-4):
            off.append('%s is %s in %s and %s in the declared target' % (k, 'not set' if have is None else have, entry['file'], want))
    for o in got.get('objects') or []:
        for k, v in sorted((o.get('overrides') or {}).items()):
            want = target.get(project_consistency.target_key(k))
            if isinstance(want, (int, float)) and not (isinstance(v, (int, float)) and abs(v - want) <= 5e-4):
                off.append('object %s overrides %s = %s' % (o.get('name'), k, v))
    return ('The file does not print with the declared values: ' + '; '.join(off[:4])) if off else None


# gate.py owner table TABLE [ROW]: what a project records of its own in gate/owner/tables.json (gate_registry.project_table_errors)
TABLE_ROWS = {
    'inserts': 'a print of the profile of insert kind ROW, made from the .3mf project --under: appended to "inserts" "measured_under" ROW',
    'seat_fit': 'a print of the bearing seat fit, made from the .3mf project --under: appended to "bearings" "seat_fit" "measured_under"',
    'bearings': 'the bearing ROW of the batch in hand, measured: --value "d,D,B" -> "bearings" "measured" ROW',
    'pins': 'the pins of diameter ROW the project stocks: --value "16,20" -> "pins" "sizes" ROW "lengths"',
    'screws': 'the screws ROW ("M3") the project stocks: --value "8,10,12" -> "screws" "stock_lengths" ROW',
    'screws_set': 'the set screws ROW ("M3") the project stocks: --value "3,4" -> "screws" "stock_lengths_set" ROW',
}


def _numbers_of(text, what, n=None):
    try:
        out = [float(x) for x in str(text or '').replace(';', ',').split(',') if x.strip()]
    except ValueError:
        out = []
    if not out or (n is not None and len(out) != n) or any(not (x > 0) for x in out):
        raise GateError('%s takes --value with %s positive number(s) in mm, separated by commas; got %r; nothing was written'
                        % (what, n if n is not None else 'one or more', text))
    return [int(x) if x == int(x) else x for x in out]


def _owner_table(root, args):
    """One row of gate/owner/tables.json (TABLE_ROWS): a print of a table row (with the declared target of the project
    it was printed from, checked against that file like `owner param --tag M`), a measured bearing, a stock list. The
    whole file is checked as the registry reads it (gate_registry.project_table_errors) before it is written; the
    command, its quote and the date are kept under "_records" (a key the registry does not read)."""
    import gate_judges, gate_params, gate_registry
    table, row = args.table, getattr(args, 'row', None)
    if table not in TABLE_ROWS:
        raise GateError('unknown table %r (known: %s); nothing was written' % (table, ', '.join(TABLE_ROWS)))
    if table != 'seat_fit' and not row:
        raise GateError('owner table %s needs the row: %s; nothing was written' % (table, TABLE_ROWS[table]))
    if table == 'seat_fit' and row:
        raise GateError('owner table seat_fit takes no row (one seat fit per project); nothing was written')
    path = _owner_file(root, 'tables.json')
    raw = gate_common.read_json(path, {})
    if not isinstance(raw, dict):
        raise GateError('gate/owner/tables.json must be an object of table -> rows')
    data = copy.deepcopy(raw)
    base = gate_registry.load(judges=set(gate_judges.JUDGES))
    if row is not None and table in ('pins', 'bearings', 'screws', 'screws_set'):
        row = gate_registry.table_row_key(table, row, base)      # one spelling per row ("5.0" is "5", an alias is its designation)
    today = datetime.date.today().isoformat()
    source = str(args.source or '').strip()
    if not source:
        raise GateError('--source must say where it comes from (the calliper, the coupon, the order); nothing was written')
    value = getattr(args, 'value', None)
    under = getattr(args, 'under', None)
    if table in ('inserts', 'seat_fit'):
        if value is not None:
            raise GateError('a print takes no --value: what it was printed under is the declared target of --under; nothing was written')
        project = gate_common.load_project(root)
        targets = project.get('targets') or {}
        key = under or (list(targets)[0] if len(targets) == 1 else None)
        if key is None or key not in targets:
            raise GateError('a print is recorded with what it was printed under. Name the .3mf project it was printed from with --under KEY '
                            '(declared targets: %s); nothing was written' % (', '.join(sorted(targets)) or 'none'))
        entry = {k: targets[key].get(k) for k in gate_params.MEASURED_UNDER}
        missing = [k for k in gate_params.MEASURED_UNDER if entry[k] is None]
        if missing:
            raise GateError('the declared target of %s does not say %s: the owner adds what the project prints with to gate/project.json "targets" '
                            'first; nothing was written' % (key, ', '.join(missing)))
        why = _printed_as_declared(root, key, targets[key])
        if why:
            raise GateError('a print is stored with what the project %s prints with. %s. Nothing was written: put the project file and its declared '
                            'target in step first (gate.py verify), then record the print' % (key, why))
        entry.update(source=source, date=today)
        if table == 'inserts':
            data.setdefault('inserts', {}).setdefault('measured_under', {}).setdefault(row, []).append(entry)
        else:
            data.setdefault('bearings', {}).setdefault('seat_fit', {}).setdefault('measured_under', []).append(entry)
        said = '%s %s: printed under %s (%s)' % (table, row or '', key, ', '.join('%s %s' % (k, entry[k]) for k in gate_params.MEASURED_UNDER))
    else:
        if under:
            raise GateError('--under goes with a print (inserts, seat_fit), not with %s; nothing was written' % table)
        def put(rows, value):
            # the row under its one key; the same row under another spelling (an earlier command) is replaced by this one
            for k in [k for k in rows if k != row and gate_registry.table_row_key(table, k, base) == row]:
                rows.pop(k)
            rows[row] = value
        if table == 'bearings':
            d, D, B = _numbers_of(value, 'a measured bearing', 3)
            put(data.setdefault('bearings', {}).setdefault('measured', {}), {'d': d, 'D': D, 'B': B, 'source': source, 'date': today})
            said = 'bearing %s measured: d %s, D %s, B %s' % (row, d, D, B)
        elif table == 'pins':
            lengths = _numbers_of(value, 'a pin stock row')
            put(data.setdefault('pins', {}).setdefault('sizes', {}), {'lengths': sorted(lengths), 'source': source, 'date': today})
            said = 'pins D%s in stock: %s' % (row, ', '.join(str(x) for x in sorted(lengths)))
        else:
            lengths = _numbers_of(value, 'a screw stock row')
            put(data.setdefault('screws', {}).setdefault('stock_lengths' if table == 'screws' else 'stock_lengths_set', {}), sorted(lengths))
            said = '%s %s in stock: %s' % ('screws' if table == 'screws' else 'set screws', row, ', '.join(str(x) for x in sorted(lengths)))
    errors = gate_registry.project_table_errors({k: v for k, v in data.items()}, base)
    if errors:
        raise GateError('table row refused, nothing written:\n  ' + '\n  '.join(errors))
    record = {'table': table, 'row': row, 'value': value, 'under': under, 'source': source, 'date': today, 'quote': getattr(args, 'quote', None)}
    data['_records'] = list(data.get('_records') or []) + [{k: v for k, v in record.items() if v is not None}]
    gate_common.write_json(path, data)
    print(said)
    print('stored judgements were made with the old tables: python3 gate/tools/gate.py run --offline judges again only the checks that read this table')
    return EXIT_PASS


def _owner_table_dry(root, args):
    """`owner table ... --dry-run`: what the row would change, as a diff; nothing is written and no quote is needed."""
    stage = _Stage()
    with stage:
        _owner_table(root, args)
    changes = stage.diff(root)
    for _rel, text in changes:
        print(text.rstrip('\n'))
    print('DRY RUN: nothing was written. %d file(s) would change: %s' % (len(changes), ', '.join(r for r, _ in changes) or 'none'))
    return EXIT_PASS


OUTSIDE_ABOUT = ('OWNER file. Objects of a registered .3mf project that no registered document or generator makes (a part of a '
                 'document the gate does not cover yet), declared outside the gate by the owner, per project key and object name, '
                 'each with the geom_hash of its mesh as the saved .3mf held it. The guard lets such an object through a save only '
                 'while its mesh is that one; any other mesh under the name is judged like any new mesh. Written only by '
                 '`gate.py owner outside`.')


def _gated_objects(root, entry, layout, names):
    """{object name: why} for the named objects of the project `entry` that ARE parts of the gate: tied to a part of the
    census or a registered generator by a write of the journal, by their geometry or by the name of its STL
    (probes.project_consistency.measure, light), or named like a part of the census or a generator. Such an object
    must be stamped; it is never declared outside the gate."""
    from probes import project_consistency
    census = gate_common.load_census(root)
    declared = (gate_common.load_project(root).get('targets') or {}).get(entry.get('key'))
    facts = project_consistency.measure(root, entry, layout, census, declared_target=declared, light=True)
    out = {}
    how = {'write': 'a write of the journal ties its mesh to %s', 'geometry': 'its mesh is the geometry of %s', 'name': 'its name is that of %s'}
    for row in facts.get('objects') or []:
        if row.get('name') in names and row.get('part'):
            stl = row.get('stl') or (row.get('hint') or {}).get('stl')
            why = how[row['tie']] % stl if stl and row.get('tie') in how else 'a stamp holds its geometry'
            out.setdefault(row['name'], 'it is the part %s: %s' % (row['part'], why))
    keys = {}
    for pid, e in sorted((census.get('parts') or {}).items()):
        if isinstance(e, dict) and not e.get('gone'):
            for text in (e.get('name'), project_consistency._bambu_name(e.get('name') or ''), e.get('stl')):
                if text:
                    keys.setdefault(project_consistency._name_key(text), pid)
    for g in layout.get('generators') or []:
        if isinstance(g, dict) and isinstance(g.get('part'), str):
            for text in (g['part'].split('/')[-1], g.get('stl')):
                if text:
                    keys.setdefault(project_consistency._name_key(text), g['part'])
    for name in names:
        pid = keys.get(project_consistency._name_key(name))
        if pid and name not in out:
            out[name] = 'its name is that of the part %s of the gate' % pid
    return out


def _owner_outside(root, args):
    """gate.py owner outside PROJECT OBJECT... --reason R --quote Q: declare objects of the registered .3mf project PROJECT
    outside the gate's coverage (gate/owner/outside.json): parts of a document the gate does not register, that can
    never get a stamp. Each entry holds the geom_hash of the object's mesh as the saved file holds it now (meshid, the
    stored vertices: the form the guard reads at every save), its triangles and plate, the reason, the quote, the date
    and the sha256 of the file. The guard lets the object through a save only while its mesh in the archive is that one.
    Refused (nothing written): a project gate/layout.json does not register, a file that cannot be read, a name the file
    does not hold or holds twice with different meshes, an object that is a part of the gate (_gated_objects: it must be
    stamped), and in a bundle a mesh other than the one its dry run showed ("expect"). Declaring a name again replaces
    its entry (the old hash is kept under "was")."""
    meshid = gate_common.sibling('meshid')
    reason = str(getattr(args, 'reason', None) or '').strip()
    if not reason:
        raise GateError('--reason must say why these objects lie outside the gate (which document makes them, and why it is not registered); nothing was written')
    names = list(dict.fromkeys(str(n) for n in getattr(args, 'objects', None) or []))
    if not names or any(not n.strip() for n in names):
        raise GateError('name each object as the .3mf shows it; nothing was written')
    layout = gate_common.load_layout(root)
    keys = [e.get('key') for e in layout.get('projects') or [] if isinstance(e, dict)]
    entry = next((e for e in layout.get('projects') or [] if isinstance(e, dict) and e.get('key') == args.project), None)
    if entry is None or not entry.get('file'):
        raise GateError('%s is no .3mf project of gate/layout.json (registered: %s); nothing was written' % (args.project, ', '.join(k for k in keys if k) or 'none'))
    path = entry['file'] if os.path.isabs(entry['file']) else os.path.join(root, *str(entry['file']).split('/'))
    rel_file = gate_common.rel(root, path)
    try:
        meshes = meshid.read_project(path)
        with open(path, 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        raise GateError('the project file %s cannot be read (%s: %s); nothing was written' % (rel_file, type(e).__name__, e))
    by = {}
    for m in meshes:
        by.setdefault(m['object'], []).append(m)
    found = {}
    for name in names:
        ms = by.get(name)
        if not ms:
            raise GateError('%s holds no object "%s" (its objects: %s); nothing was written' % (rel_file, name, ', '.join(sorted(by)) or 'none'))
        hashes = {meshid.geom_hash(m['vertices'], len(m['triangles'])) for m in ms}
        if len(hashes) > 1:
            raise GateError('%s holds %d objects named "%s" with different meshes: give them names of their own first; nothing was written' % (rel_file, len(ms), name))
        found[name] = {'geom_hash': hashes.pop(), 'triangles': len(ms[0]['triangles']), 'plate': ms[0].get('plate'), 'plate_index': ms[0].get('plate_index')}
    gated = _gated_objects(root, entry, layout, names)
    if gated:
        raise GateError('a part of a registered document or generator is never outside the gate: it is exported through the gate (gate.py export) '
                        'and stamped. Nothing was written:\n  ' + '\n  '.join('"%s": %s' % (n, gated[n]) for n in names if n in gated))
    expect = getattr(args, 'expect', None)
    if expect is not None:                               # a bundle entry: the meshes the owner was shown in its dry run
        moved = [n for n in names if (expect.get(n) if isinstance(expect, dict) else None) != found[n]['geom_hash']]
        if moved:
            raise GateError('the mesh of %s in %s is not the one the entry names ("expect": %s, the file: %s); nothing was written. Show the owner '
                            'the dry run again' % (', '.join('"%s"' % n for n in moved), rel_file,
                                                   ', '.join(str((expect or {}).get(n))[:12] for n in moved), ', '.join(found[n]['geom_hash'][:12] for n in moved)))
    target = gate_common.outside_path(root)
    data = gate_common.load_outside(root)
    data.setdefault('_about', OUTSIDE_ABOUT)
    project = data['projects'].setdefault(args.project, {})
    project['file'] = rel_file
    objects = project.setdefault('objects', {})
    ticket = getattr(args, '_ticket', None)
    today = datetime.date.today().isoformat()
    for name in names:
        old = objects.get(name)
        e = dict(found[name], reason=reason, quote=args.quote, date=today, file_sha256=sha,
                 was={k: old.get(k) for k in ('geom_hash', 'date', 'reason')} if isinstance(old, dict) else None)
        if isinstance(ticket, dict) and isinstance(ticket.get('turn'), dict):
            e['turn'] = ticket['turn']                   # the owner turn the hook found the quote in (uuid, time)
        objects[name] = e
        print('declared outside the gate in %s: "%s" (plate %s), mesh %s, %d triangles%s'
              % (args.project, name, found[name]['plate_index'] if found[name]['plate_index'] is not None else 'none', found[name]['geom_hash'][:12],
                 found[name]['triangles'], ' (was %s of %s)' % (str(old.get('geom_hash'))[:12], old.get('date')) if isinstance(old, dict) else ''))
    gate_common.write_json(target, data)
    print('each passes a save of %s only while its mesh is this one, not checked and without a stamp, and status, verify and the session hooks '
          'list it; any other mesh under its name is judged like a new mesh (enforce: refused without a stamp)' % rel_file)
    return EXIT_PASS


def _owner_outside_dry(root, args):
    """`owner outside ... --dry-run`: what the declaration would write, as a diff, and the bundle entry that applies the
    same (with the meshes as "expect"); nothing is written and no quote is needed. The hook keeps the dry run bound to
    the file, the objects and the reason (gate_watch.outside_binding): the apply needs the owner's answer to it."""
    args.quote = '<the owner\'s words: --quote at the apply>'
    stage = _Stage()
    with stage:
        _owner_outside(root, args)
        data = gate_common.load_outside(root)
    objects = ((data.get('projects') or {}).get(args.project) or {}).get('objects') or {}
    names = list(dict.fromkeys(str(n) for n in args.objects))
    changes = stage.diff(root)
    for _rel, text in changes:
        print(text.rstrip('\n'))
    print('bundle entry: %s' % json.dumps({'kind': 'outside', 'project': args.project, 'objects': names, 'reason': str(args.reason).strip(),
                                            'expect': {n: objects[n]['geom_hash'] for n in names}}, sort_keys=True))
    print('DRY RUN: nothing was written. %d file(s) would change: %s' % (len(changes), ', '.join(r for r, _ in changes) or 'none'))
    return EXIT_PASS


def cmd_owner(root, args):
    """gate.py owner waive | decide | param: the only writer of gate/owner/*. Every one needs --quote (the owner's own
    words) and is refused while the project's .claude/settings.json lacks the `ask` rules that put a prompt in front of
    the owner (gate_common.ask_rules_missing): with them, the owner sees each call. Returns 0, or 4 when the input is
    refused (nothing is written then).

    waive   args.finding is the id of an open finding (gate_features.finding_id) of the latest report; the new entry of
            gate/owner/waivers.json takes rule, part, anchor and context FROM THAT FINDING (never from the command
            line), plus reason, quote, today's date and "id": "W-<n>". Refused when gate_features.validate_waiver()
            objects (an export order as the reason, an empty quote) or the finding is not open.
            The id may also be that of an open question (Q-...) whose rule takes a waiver as evidence.
    decide  args.item is the id of an item of gate/owner/triage.json; sets "decision", "quote", "note", "date".
            While the owner has no list yet, the first decision adopts gate/proposals/triage.json (the audit) as it.
            With the id of a group (G-..., the same kind of finding on every part: rule + cause) it decides every row
            of that group that has no decision yet; args.part narrows it to one part.
    adopt   takes over what the proposal of the audit holds and the owner's list does not: its findings as items, its
            parts as the baseline of inherited parts (gate_report: "Inherited parts"). The first call makes the list;
            a call after a later audit adds what that one found.
    param   sets gate/owner/params.json[args.name] = {"value", "tag", "source", "date", "quote"} (args.key: under "per"); the
            value is parsed as a number when the registry default is one; refused when gate_params.check() objects.
            A fit parameter tagged M also gets "measured_under": nozzle, material, X-Y hole compensation and layer height
            of the declared target of the .3mf project it was printed from (args.under, or the only declared target).
            That project file must exist and hold those values today (_printed_as_declared): the coupon was printed
            from the file. A per-size value (--key) cannot be M while the base value of the parameter is not.
    baseline  gate/owner/support_baseline.json["projects"][args.project][object] = the support the last slice of the
            gate measured for that object (gate_slice.owner_baseline); refused for an object not sliced from the mesh
            the project holds now.
    outside   gate/owner/outside.json["projects"][args.project]["objects"][object] = the mesh hash the saved .3mf holds
            for each named object now, with reason and quote (_owner_outside): objects no registered document makes,
            which the guard then lets through only while their mesh is that one. --dry-run shows the change. Refused
            for an object that is a part of the gate (it is exported and stamped, never declared)."""
    if args.action == 'bundle' and getattr(args, 'dry_run', False):
        return _owner_bundle(root, args)         # writes nothing: no prompt, no quote needed to show what would change
    if args.action == 'table' and getattr(args, 'dry_run', False):
        return _owner_table_dry(root, args)
    if args.action == 'outside' and getattr(args, 'dry_run', False):
        return _owner_outside_dry(root, args)
    if _agent_session():
        args._ticket = _take_ticket(root, args)
    missing = gate_common.ask_rules_missing(root)
    if missing:
        raise GateError('the owner commands write the owner\'s files, and nothing in this project puts a prompt in front of him first. '
                        'Missing in .claude/settings.json: %s. The owner adds the "permissions" of the plugin\'s templates/gate/settings.json '
                        '(setup_project.py --gate prints them); nothing was written' % '; '.join(missing))
    if not str(getattr(args, 'quote', '') or '').strip():
        raise GateError('an owner command needs --quote: the owner\'s own words for this decision; nothing was written')
    return {'waive': _owner_waive, 'decide': _owner_decide, 'adopt': _owner_adopt, 'param': _owner_param, 'bundle': _owner_bundle, 'baseline': _owner_baseline,
            'table': _owner_table, 'outside': _owner_outside}[args.action](root, args)


AGENT_ENV = ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID')


def _agent_session():
    """Does this process run inside an agent session (Claude Code sets these in every command it runs)?"""
    return any(os.environ.get(k) for k in AGENT_ENV)


def _take_ticket(root, args):
    """An owner command from an agent session needs the one-time ticket the PreToolUse hook (gate_watch.py) leaves in
    gate/run/owner_tickets.json when the quote is the owner's own words: the same arguments from "owner" on, the same
    quote, and for a bundle the proposal unchanged since. The ticket is used up. GateError when there is none: the
    command did not go through the hook as a plain `gate.py owner ...` (an import of gate.py, `python3 -m gate`, a script
    file), the hooks are not live, or the quote did not hold."""
    import gate_watch
    argv = list(getattr(args, '_argv', None) or [])
    if 'owner' not in argv:
        raise GateError('an owner command from an agent session needs its arguments as the hook saw them; nothing was written')
    tail = argv[argv.index('owner'):]
    key = gate_watch.ticket_key(tail)
    path = gate_common.gate_path(root, *gate_watch.TICKETS)
    try:
        with open(path, encoding='utf-8') as f:
            book = json.load(f)
    except (OSError, ValueError):
        book = {}
    now = time.time()
    tickets = [t for t in (book.get('tickets') if isinstance(book, dict) else None) or [] if isinstance(t, dict) and now - float(t.get('ts') or 0) < gate_watch.TICKET_S]
    hit = next((t for t in tickets if t.get('key') == key), None)
    why = None
    if hit is None:
        why = ('no ticket of the print gate\'s hook for exactly this command. From an agent session an owner command runs only as a plain '
               '`python3 gate/tools/gate.py owner ... --quote "<his words>"` call that the PreToolUse hook checked against the owner\'s own turns '
               '(not through python -m, an import or a script file; within %d s of the check). The owner can also run it in his own terminal' % gate_watch.TICKET_S)
    elif gate_watch.normalize(args.quote or '') not in (hit.get('quotes') or []):
        why = 'the --quote of this command is not the one the hook checked'
    elif args.action == 'outside':                   # bound to what the dry run showed: the file, the objects, the reason
        if not hit.get('proposal_sha256') or gate_watch.outside_binding(root, args.project, args.objects, args.reason) != hit['proposal_sha256']:
            why = ('the project file, the objects or the reason changed after the hook checked the quote against the dry run the owner saw: '
                   'show him the dry run again')
    elif hit.get('proposal_sha256'):
        prop = args.proposal if os.path.isabs(args.proposal) else os.path.join(os.getcwd(), args.proposal)
        try:
            with open(prop, 'rb') as f:
                sha = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            sha = None
        if sha != hit['proposal_sha256']:
            why = 'the proposal changed after the hook checked the quote against the dry run the owner saw'
    if hit is not None:
        tickets.remove(hit)
        try:
            gate_common.write_json(path, dict(book if isinstance(book, dict) else {}, tickets=tickets))
        except Exception:
            pass
    if why:
        raise GateError(why + '; nothing was written')
    return hit


# ---- owner bundle: a prepared proposal applied in one call ---------------------------------------------------------------

BUNDLE_KINDS = {                                 # kind -> (required fields, optional fields)
    'param': (('name', 'value', 'tag', 'source'), ('key', 'under')),
    'target': (('project', 'set'), ('object', 'unset')),
    'waive': (('finding', 'reason'), ()),
    'decide': (('item', 'decision'), ('note', 'part')),
    'adopt': ((), ()),
    'mode': (('mode',), ()),
    'questions': (('questions',), ()),                               # "on" | "off": no questions, measurement alone (gate_common.questions_off)
    'deps': (('deps',), ()),                                         # which record of what a unit read decides (gate_deps.deps_mode)
    'baseline': (('project', 'objects', 'expect'), ('reason',)),     # expect: {object: {"support_e", "top_z", "pose"}} as the dry run shows them
    'table': (('table', 'source'), ('row', 'value', 'under')),       # a row of gate/owner/tables.json, as `owner table`
    'outside': (('project', 'objects', 'reason', 'expect'), ()),      # expect: {object: geom_hash} as `owner outside --dry-run` shows them
}
BUNDLE_COMMON = ('kind', 'why')                  # "why": the evidence, for the owner to read; never applied


def read_bundle(path):
    """The entries of a proposal file: a .json file {"bundle": 1, "title", "entries": [...]}, or a Markdown file that
    holds that object in a fenced block (```json gate-bundle, else the only ```json block). Every entry is checked for
    its kind and fields here (BUNDLE_KINDS); GateError names the first problem."""
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError as e:
        raise GateError('the proposal %s cannot be read (%s)' % (path, e))
    if not path.lower().endswith('.json'):
        blocks = re.findall(r'^```json([^\n]*)\n(.*?)^```', text, re.S | re.M)
        tagged = [b for tag, b in blocks if 'gate-bundle' in tag]
        if len(tagged) == 1:
            text = tagged[0]
        elif len(blocks) == 1 and not tagged:
            text = blocks[0][1]
        else:
            raise GateError('%s must hold exactly one fenced block ```json gate-bundle (found %d json block(s), %d tagged)' % (path, len(blocks), len(tagged)))
    try:
        data = json.loads(text)
    except ValueError as e:
        raise GateError('the bundle in %s is not valid JSON: %s' % (path, e))
    if not isinstance(data, dict) or data.get('bundle') != 1 or not isinstance(data.get('entries'), list) or not data['entries']:
        raise GateError('the bundle in %s must be {"bundle": 1, "title": "...", "entries": [at least one entry]}' % path)
    for i, e in enumerate(data['entries'], 1):
        if not isinstance(e, dict) or e.get('kind') not in BUNDLE_KINDS:
            raise GateError('entry %d: "kind" must be one of %s' % (i, ', '.join(BUNDLE_KINDS)))
        need, may = BUNDLE_KINDS[e['kind']]
        missing = [k for k in need if e.get(k) is None]
        extra = sorted(k for k in e if k not in need + may + BUNDLE_COMMON)
        if missing or extra:
            raise GateError('entry %d (%s): %s' % (i, e['kind'], '; '.join((['missing ' + ', '.join(missing)] if missing else []) + (['unknown ' + ', '.join(extra)] if extra else []))))
        if e['kind'] == 'target' and (not isinstance(e['set'], dict) or not isinstance(e.get('unset') or [], list)):
            raise GateError('entry %d (target): "set" must be an object of key -> value, "unset" a list of keys' % i)
        if e['kind'] == 'mode' and e['mode'] not in ('observe', 'enforce'):
            raise GateError('entry %d (mode): "mode" must be "observe" or "enforce"' % i)
        if e['kind'] == 'questions' and e['questions'] not in ('on', 'off'):
            raise GateError('entry %d (questions): "questions" must be "on" or "off"' % i)
        if e['kind'] == 'deps' and e['deps'] not in ('wide', 'tight'):
            raise GateError('entry %d (deps): "deps" must be "wide" or "tight"' % i)
        if e['kind'] == 'baseline' and (not isinstance(e['expect'], dict) or not isinstance(e['objects'], list)
                                        or any(not isinstance(e['expect'].get(o), dict) for o in e['objects'])):
            raise GateError('entry %d (baseline): "expect" must give each object of "objects" the numbers the owner is shown '
                            '({"support_e", "top_z", "pose"} from gate/proposals/support_baseline.json): the apply refuses when the slice record moved since' % i)
        if e['kind'] == 'table' and e['table'] not in TABLE_ROWS:
            raise GateError('entry %d (table): "table" must be one of %s' % (i, ', '.join(TABLE_ROWS)))
        if e['kind'] == 'table' and e.get('row') is not None and not isinstance(e['row'], str):
            raise GateError('entry %d (table): "row" must be a text, as on the command line ("5", "608", "M3"), not %r' % (i, e['row']))
        if e['kind'] == 'outside' and (not isinstance(e['objects'], list) or not e['objects'] or any(not isinstance(o, str) for o in e['objects'])
                                       or not isinstance(e['expect'], dict) or any(not gate_common.OUTSIDE_HASH.match(str(e['expect'].get(o) or '')) for o in e['objects'])):
            raise GateError('entry %d (outside): "objects" must be a list of object names and "expect" give each of them the mesh hash the owner is '
                            'shown (the "bundle entry" line of `gate.py owner outside ... --dry-run`): the apply refuses when the mesh moved since' % i)
        if e['kind'] == 'decide' and e['decision'] not in DECISIONS:
            raise GateError('entry %d (decide): "decision" must be one of %s' % (i, ', '.join(DECISIONS)))
    return data


class _Stage(object):
    """Every gate_common.read_json / write_json of this process goes through a staging area while it is active: writes
    are kept in memory, reads see them. Nothing reaches the disk until commit(). So a bundle is applied entry by entry
    with the owner commands as they are, and either every entry holds or nothing is written; a dry run is the same
    run without the commit, and no other process ever sees a half-applied bundle."""
    _MISSING = object()

    def __init__(self):
        self.files, self.patched = {}, []
        self.read0, self.write0 = gate_common.read_json, gate_common.write_json

    def read(self, path, default=gate_common._MISSING):
        key = os.path.abspath(path)
        if key in self.files:
            return copy.deepcopy(self.files[key])
        return self.read0(path, default) if default is not gate_common._MISSING else self.read0(path)

    def write(self, path, data):
        self.files[os.path.abspath(path)] = copy.deepcopy(data)
        return path

    def __enter__(self):
        for m in list(sys.modules.values()):
            for name, fn in (('read_json', self.read), ('write_json', self.write)):
                if m is not None and getattr(m, name, None) is (self.read0 if name == 'read_json' else self.write0):
                    self.patched.append((m, name, getattr(m, name)))
                    setattr(m, name, fn)
        return self

    def __exit__(self, *exc):
        for m, name, fn in self.patched:
            setattr(m, name, fn)
        self.patched = []
        for m in list(sys.modules.values()):             # a module first imported inside the stage took the staged functions
            for name, fn in (('read_json', self.read0), ('write_json', self.write0)):
                if m is not None and getattr(m, name, None) in (self.read, self.write):
                    setattr(m, name, fn)
        return False

    def diff(self, root):
        """[(relative path, unified diff text)] of every staged file against the disk."""
        out = []
        for path in sorted(self.files):
            try:
                with open(path, encoding='utf-8') as f:
                    old = f.read()
            except OSError:
                old = ''
            new = json.dumps(self.files[path], indent=1, sort_keys=True, ensure_ascii=False) + '\n'
            if old != new:
                rel = gate_common.rel(root, path)
                out.append((rel, ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), 'a/' + rel, 'b/' + rel, n=1))))
        return out

    def commit(self, root):
        """Write every staged file (gate/project.json last: a mode switch never lands before the numbers it rests on)."""
        project = os.path.abspath(gate_common.gate_path(root, 'project.json'))
        for path in sorted(self.files, key=lambda p: (p == project, p)):
            self.write0(path, self.files[path])


def _owner_target(root, args):
    """One "target" entry: set (and unset) keys of the declared target of the .3mf project args.project in
    gate/project.json, or of one object of it under "objects" (args.object). A "print" block is merged key by key.
    Refused when the project is not registered in gate/layout.json or gate_params.check() objects."""
    import gate_params, gate_registry
    reg = gate_registry.load_default(root)
    keys = [e.get('key') for e in gate_common.load_layout(root).get('projects') or [] if isinstance(e, dict)]
    if args.project not in keys:
        raise GateError('%s is no .3mf project of gate/layout.json (registered: %s)' % (args.project, ', '.join(k for k in keys if k) or 'none'))
    path = gate_common.gate_path(root, 'project.json')
    data = gate_common.read_json(path, {})
    if not isinstance(data, dict):
        raise GateError('gate/project.json must be an object')
    targets = dict(data.get('targets') or {})
    target = copy.deepcopy(targets.get(args.project) or {})
    where = target
    if args.object:
        objects = target.setdefault('objects', {})
        where = objects.setdefault(args.object, {})
    for k, v in sorted(args.set.items()):
        if k == 'print' and isinstance(v, dict) and isinstance(where.get('print'), dict):
            where['print'] = dict(where['print'], **v)
        else:
            where[k] = v
    for k in args.unset or []:
        where.pop(k, None)
    if args.object and not where:
        target['objects'].pop(args.object, None)
    if 'objects' in target and not target['objects']:
        target.pop('objects')
    errors = gate_params.check(reg, {}, target)
    if errors:
        raise GateError('declared target of %s refused:\n  %s' % (args.project, '\n  '.join(errors)))
    targets[args.project] = target
    data['targets'] = targets
    gate_common.write_json(path, data)
    print('declared target of %s%s: %s' % (args.project, ' (object %s)' % args.object if args.object else '',
                                           ', '.join('%s = %s' % (k, json.dumps(v)) for k, v in sorted(args.set.items())) + (('; removed ' + ', '.join(args.unset)) if args.unset else '')))
    return EXIT_PASS


def _owner_mode(root, args):
    path = gate_common.gate_path(root, 'project.json')
    data = gate_common.read_json(path, {})
    if not isinstance(data, dict):
        raise GateError('gate/project.json must be an object')
    old = data.get('mode', 'observe')
    data['mode'] = args.mode
    gate_common.write_json(path, data)
    print('mode: %s -> %s' % (old, args.mode))
    if args.mode == 'enforce':
        try:
            import gate_guard
            saves = gate_common.read_json(gate_guard.saves_path(root), {}).get('projects') or {}
        except Exception:
            saves = {}
        for f, e in sorted(saves.items()):
            names = (e or {}).get('unchecked') or []
            if names:
                print('  WARNING %s: %d object(s) have no stamp at its last guarded save: enforce lets nothing through because it was there before, '
                      'so every save of this file (a settings-only or a plain re-save too) is refused until they are exported through the gate '
                      '(gate.py export PART) and swapped in' % (f, len(names)))
    return EXIT_PASS


def _owner_questions(root, args):
    """gate/project.json "questions": "off" makes the gate decide by measurement alone (gate_common.questions_off): no
    question is asked, a finding that would ask is an open row, `attest answer` is refused. "on" is the default."""
    path = gate_common.gate_path(root, 'project.json')
    data = gate_common.read_json(path, {})
    if not isinstance(data, dict):
        raise GateError('gate/project.json must be an object')
    old = data.get('questions', 'on')
    data['questions'] = args.questions
    gate_common.write_json(path, data)
    print('questions: %s -> %s' % (old, args.questions))
    return EXIT_PASS


def _owner_deps(root, args):
    """Which record of what a unit read decides whether it is measured again (gate/project.json "deps", gate_deps.py).
    "tight" is refused while the shadow record (gate_deps.shadow_summary over gate/run/*/deps_shadow.jsonl) holds a
    violation, no unit at all, or no unit of one of the probes that note tight regions (gate_deps.tight_probes): the
    switch rests on evidence that the tight record missed nothing, for every probe it decides for. "wide" is always
    taken (it measures more, never less)."""
    import gate_deps
    path = gate_common.gate_path(root, 'project.json')
    data = gate_common.read_json(path, {})
    if not isinstance(data, dict):
        raise GateError('gate/project.json must be an object')
    s = gate_deps.shadow_summary(root)
    said = '%d shadow unit(s) over %d run(s), %d part(s) in %d document(s): %d gave the same facts, %d differed' % (
        s['units'], s['runs'], len(s['parts']), len(s['docs']), s['same'], len(s['violations']))
    if args.deps == 'tight':
        if s['violations']:
            bad = s['violations'][0]
            raise GateError('"deps": "tight" is refused: %s (the first: %s %s, %s). The tight record would have missed what changed those facts: '
                            'report it; "wide" stays' % (said, bad.get('part'), bad.get('probe'), ', '.join(bad.get('keys') or []) or bad.get('why') or '?'))
        if not s['units']:
            raise GateError('"deps": "tight" is refused: the shadow record holds no unit yet, nothing proves the tight record. Runs with "wide" '
                            'write it (gate/run/<run>/deps_shadow.jsonl)')
        untried = [p for p in gate_deps.tight_probes() if not s['by_probe'].get(p)]
        if untried:
            raise GateError('"deps": "tight" is refused: %s; no shadow unit of %s gave the same facts yet, nothing proves their tight regions. '
                            'Runs with "wide" write them (gate/run/<run>/deps_shadow.jsonl)' % (said, ', '.join(untried)))
    old = data.get('deps', 'wide')
    data['deps'] = args.deps
    gate_common.write_json(path, data)
    print('deps: %s -> %s (%s)' % (old, args.deps, said))
    return EXIT_PASS


def _owner_bundle(root, args):
    """gate.py owner bundle PROPOSAL --quote "...": apply every entry of a prepared proposal (read_bundle) with the
    owner commands, under one quote, in one call: either every entry holds and every file is written, or nothing is.
    --dry-run prints what each entry does and the exact change of every file, and writes nothing. A bundle that was
    applied is kept in gate/owner/bundles.json (id, date, quote, proposal path and its sha256, entries)."""
    if os.path.basename(str(args.proposal)).startswith('-'):
        raise GateError('a proposal path may not start with "-" (%s): rename the file; nothing was written' % args.proposal)
    path = args.proposal if os.path.isabs(args.proposal) else os.path.join(os.getcwd(), args.proposal)
    data = read_bundle(path)
    dry = bool(getattr(args, 'dry_run', False))
    quote = args.quote if args.quote and str(args.quote).strip() else ('<the owner\'s words: --quote at the apply>' if dry else None)
    if quote is None:
        raise GateError('an owner command needs --quote: the owner\'s own words for this decision; nothing was written')
    with open(path, 'rb') as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    funcs = {'param': _owner_param, 'target': _owner_target, 'waive': _owner_waive, 'decide': _owner_decide, 'adopt': _owner_adopt, 'mode': _owner_mode, 'questions': _owner_questions, 'deps': _owner_deps, 'baseline': _owner_baseline,
             'table': _owner_table, 'outside': _owner_outside}
    print('%s %s: %d entr%s%s' % ('DRY RUN of' if dry else 'bundle', gate_common.rel(root, path), len(data['entries']), 'y' if len(data['entries']) == 1 else 'ies',
                                  (' - ' + data['title']) if data.get('title') else ''))
    stage = _Stage()
    with stage:
        for i, e in enumerate(data['entries'], 1):
            need, may = BUNDLE_KINDS[e['kind']]
            ns = argparse.Namespace(quote=quote, **{k: e.get(k) for k in need + may})
            if e['kind'] == 'param':
                ns.value = e['value'] if isinstance(e['value'], str) else repr(e['value']) if isinstance(e['value'], float) else str(e['value'])
            if e['kind'] == 'table' and isinstance(e.get('value'), list):
                ns.value = ','.join(str(x) for x in e['value'])
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    funcs[e['kind']](root, ns)
            except GateError as err:
                raise GateError('entry %d (%s) refused: %s. Nothing of the bundle was written' % (i, e['kind'], err))
            what = out.getvalue().strip().splitlines()
            print('  %d. %s: %s' % (i, e['kind'], what[0] if what else 'done'))
            for line in what[1:]:
                print('       ' + line)
            if e.get('why'):
                print('       why: %s' % e['why'])
        try:                                     # the end state as a whole: every declared target and every parameter
            import gate_params, gate_registry
            project = gate_common.load_project(root)
            reg = gate_registry.load_default(root)
            overrides = gate_params.load_overrides(root)
            errors = []
            for key, t in sorted((project.get('targets') or {}).items()):
                errors += ['%s: %s' % (key, x) for x in gate_params.check(reg, overrides, t)]
        except GateError as err:
            errors = [str(err)]
        if errors:
            raise GateError('the bundle as a whole is refused, nothing was written:\n  ' + '\n  '.join(errors))
        book = gate_common.read_json(_owner_file(root, 'bundles.json'), {})     # staged in a dry run too: it shows every file the apply writes
        book = book if isinstance(book, dict) else {}
        done = list(book.get('bundles') or [])
        record = {'id': 'B-%d' % (len(done) + 1), 'date': datetime.date.today().isoformat(), 'quote': quote,
                  'proposal': gate_common.rel(root, path), 'sha256': sha, 'title': data.get('title'),
                  'entries': [{k: v for k, v in e.items() if k != 'why'} for e in data['entries']]}
        ticket = getattr(args, '_ticket', None)
        if isinstance(ticket, dict) and isinstance(ticket.get('turn'), dict):
            record['turn'] = ticket['turn']              # the owner turn the hook found the quote in (uuid, time)
        book['bundles'] = done + [record]
        gate_common.write_json(_owner_file(root, 'bundles.json'), book)
    changes = stage.diff(root)
    for rel, text in changes:
        print(text.rstrip('\n'))
    if dry:
        print('DRY RUN: nothing was written. %d file(s) would change: %s' % (len(changes), ', '.join(r for r, _ in changes) or 'none'))
        return EXIT_PASS
    stage.commit(root)
    print('bundle applied: %d file(s) written: %s' % (len(changes), ', '.join(r for r, _ in changes)))
    print('stored judgements were made with the old values: python3 gate/tools/gate.py run --offline judges them again')
    return EXIT_PASS


def _owner_baseline(root, args):
    import gate_slice
    return gate_slice.owner_baseline(root, args)


def _forked(root):
    """The line about a vendored copy that was changed by hand (gate/LOCK.json: "forked"), or None. A project without a
    lock (the plugin's own tools driven with --root) and a lock that cannot be read are left to `gate.py doctor`."""
    try:
        import gate_vendor
        st = gate_vendor.lock_state(root)
        return gate_vendor.state_line(st) if st.get('state') == 'forked' else None
    except Exception:
        return None


def main(argv=None):
    """Parse, find the project root, hand over to the function COMMANDS names, return its exit code.
    GateError -> the message on stderr, exit 4. A part of the gate that still raises NotImplementedError -> exit 4."""
    args = parser().parse_args(argv)
    args._argv = list(sys.argv[1:] if argv is None else argv)
    started = time.time()
    root = os.path.abspath(args.root) if args.root else find_root(os.getcwd())
    if root is not None and not os.path.isfile(os.path.join(root, 'gate', 'project.json')):
        sys.stderr.write('GATE ERROR  %s has no gate/project.json\n' % root)
        return EXIT_INFRA
    if root is None and args.command not in NO_ROOT:
        sys.stderr.write('GATE ERROR  no gate/project.json at or above %s: this folder is not gated\n' % os.getcwd())
        return EXIT_INFRA
    module, func = COMMANDS[args.command]
    try:
        if args.command in NEEDS_INTACT or (args.command == 'attest' and getattr(args, 'action', None) == 'answer'):
            forked = _forked(root)
            if forked:
                sys.stderr.write('GATE ERROR  %s\n            `gate.py %s` does not run on a changed copy: its verdicts would not be the plugin\'s. '
                                 '`gate.py doctor` lists the files\n' % (forked, args.command))
                return EXIT_INFRA
        fn = cmd_owner if module == 'gate' else getattr(importlib.import_module(module), func)
        importlib.import_module('gate_registry').use_root(root)         # the project's own table rows (gate/owner/tables.json)
        code = fn(root, args)
        _checked(root, args, code, started)
        dry = args.command == 'owner' and getattr(args, 'dry_run', False)       # a dry run changed nothing: the kept digest stays
        if root is not None and args.command in REFRESH_DIGEST and int(code or 0) != EXIT_MORE and not dry:
            importlib.import_module('gate_report').refresh_digest(root)
    except NotImplementedError:
        sys.stderr.write('GATE ERROR  "%s" is not built yet (%s.%s)\n' % (args.command, module, func))
        _checked(root, args, EXIT_INFRA, started)
        return EXIT_INFRA
    except GateError as e:
        sys.stderr.write('GATE ERROR  %s\n' % e)
        _checked(root, args, EXIT_INFRA, started)
        return EXIT_INFRA
    return int(code or 0)


def _checked(root, args, code, started=None):
    """The end of a `run` goes to gate/run/checks.jsonl: the Stop hook (gate_watch.py) reads it to know whether the parts
    changed in Fusion were checked (with the time the run started, whether it asked Fusion, and the session). Never raises."""
    if root is None or getattr(args, 'command', None) != 'run':
        return
    try:
        import gate_watch
        gate_watch.record_check(root, args, int(code or 0), started)
    except Exception:
        pass


if __name__ == '__main__':
    sys.exit(main())
