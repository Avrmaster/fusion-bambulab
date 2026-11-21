#!/usr/bin/env python3
"""The one command of the gate. Standard library only.

    python3 gate/tools/gate.py doctor                      vendored copy vs LOCK, interpreter, Fusion reachable, documents open
    python3 gate/tools/gate.py rules [--md] [--category C] effective rules with this project's numbers
    python3 gate/tools/gate.py params check | explain NAME [--target KEY]
    python3 gate/tools/gate.py scan [--doc KEY]            discover parts and signatures (short Fusion calls), update census.json
    python3 gate/tools/gate.py run [--changed | --all | --part ID ...] [--parts ID,ID] [--for KEY] [--wall 20] [--audit] [--offline] [--no-scan]
    python3 gate/tools/gate.py status [--json] [--digest]  no Fusion
    python3 gate/tools/gate.py attest list [--part ID] [--waiting] | answer Q (--label "..." | --na "why" | --param NAME | --shot PATH) [--for KEY]
    python3 gate/tools/gate.py export PART [...] [--for KEY] [--wall 20]   quarantine STL -> STL checks -> promote + stamp
    python3 gate/tools/gate.py export --generated STL [--for KEY]  a part made by code: STL checks + its attest, then stamp
    python3 gate/tools/gate.py verify [PROJECT.3mf]        stamps <-> STLs <-> meshes, project consistency; no Fusion
    python3 gate/tools/gate.py report [--last]
    python3 gate/tools/gate.py triage [--md]               inherited findings grouped for the owner
    python3 gate/tools/gate.py owner waive F-ID|Q-ID --reason "..." --quote "..."
    python3 gate/tools/gate.py owner adopt --quote "..."     the audit becomes the list of inherited findings and parts
    python3 gate/tools/gate.py owner decide T-ID|G-ID fix|waive|tolerate|rule_wrong --quote "..." [--note "..."] [--part ID]
    python3 gate/tools/gate.py owner param NAME VALUE --tag M|U|D|S --source "..." [--key SIZE] [--under KEY] --quote "..."

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
is). `owner ...` is the only writer of gate/owner/*: a project's permission rule asks the owner before each such call."""
import argparse, datetime, importlib, os, sys

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
    'report': ('gate_report', 'cmd_report'),
    'triage': ('gate_report', 'cmd_triage'),
    'owner': ('gate', 'cmd_owner'),
}
NO_ROOT = ('doctor', 'rules')                  # these also work in a folder that is not gated (root is then None)
# commands that judge, stamp or write: they do not run on a vendored copy that was changed by hand (gate/LOCK.json)
NEEDS_INTACT = ('run', 'scan', 'export', 'verify', 'owner')
# commands after which the digest the SessionStart hook prints is built again (gate_report.refresh_digest): those that
# can change the state of the gate. `status` builds it itself; a `run` or `scan` that needs more calls (exit 3) does
# not, it would add a judgement of every part to every short call: the hook then says the digest is stale.
REFRESH_DIGEST = ('scan', 'run', 'attest', 'export', 'verify', 'triage', 'owner', 'params')
DECISIONS = ('fix', 'waive', 'tolerate', 'rule_wrong')


class _Parser(argparse.ArgumentParser):
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
              target (--for KEY or None)
    status    json (bool), digest (bool)
    attest    action ("list" | "answer"), question (Q id or None), label, na, param, shot (each text or None),
              target (--for KEY or None), part (list: the questions of one part, waiting ones too), waiting (bool)
    export    parts (list of part ids), generated (STL path or None), target (--for KEY or None), wall (seconds)
    verify    project (path of a .3mf or None = every registered project)
    report    last (bool)
    triage    md (bool)
    owner     action ("waive" | "decide" | "adopt" | "param"); waive: finding, reason, quote; decide: item (T-id of a row
              or G-id of a group), decision, quote, note, part (with a G-id: only the rows of this part); adopt: quote;
              param: name, value (text as typed), tag, source, key, quote"""
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
    s.add_argument('--wall', type=float, default=20.0); s.add_argument('--audit', action='store_true'); s.add_argument('--offline', action='store_true')
    s.add_argument('--no-scan', dest='no_scan', action='store_true'); s.add_argument('--for', dest='target')
    s = sub.add_parser('status'); s.add_argument('--json', action='store_true'); s.add_argument('--digest', action='store_true')
    s = sub.add_parser('attest'); s.add_argument('action', choices=('list', 'answer')); s.add_argument('question', nargs='?')
    g = s.add_mutually_exclusive_group()
    g.add_argument('--label'); g.add_argument('--na'); g.add_argument('--param'); g.add_argument('--shot')
    s.add_argument('--for', dest='target'); s.add_argument('--part'); s.add_argument('--waiting', action='store_true')
    s = sub.add_parser('export'); s.add_argument('parts', nargs='*'); s.add_argument('--generated'); s.add_argument('--for', dest='target')
    s.add_argument('--wall', type=float, default=20.0)
    s = sub.add_parser('verify'); s.add_argument('project', nargs='?')
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
    reg = gate_registry.load_default()
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
    reg = gate_registry.load_default()
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
            from the file. A per-size value (--key) cannot be M while the base value of the parameter is not."""
    missing = gate_common.ask_rules_missing(root)
    if missing:
        raise GateError('the owner commands write the owner\'s files, and nothing in this project puts a prompt in front of him first. '
                        'Missing in .claude/settings.json: %s. The owner adds the "permissions" of the plugin\'s templates/gate/settings.json '
                        '(setup_project.py --gate prints them); nothing was written' % '; '.join(missing))
    if not str(getattr(args, 'quote', '') or '').strip():
        raise GateError('an owner command needs --quote: the owner\'s own words for this decision; nothing was written')
    return {'waive': _owner_waive, 'decide': _owner_decide, 'adopt': _owner_adopt, 'param': _owner_param}[args.action](root, args)


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
        code = fn(root, args)
        if root is not None and args.command in REFRESH_DIGEST and int(code or 0) != EXIT_MORE:
            importlib.import_module('gate_report').refresh_digest(root)
    except NotImplementedError:
        sys.stderr.write('GATE ERROR  "%s" is not built yet (%s.%s)\n' % (args.command, module, func))
        return EXIT_INFRA
    except GateError as e:
        sys.stderr.write('GATE ERROR  %s\n' % e)
        return EXIT_INFRA
    return int(code or 0)


if __name__ == '__main__':
    sys.exit(main())
