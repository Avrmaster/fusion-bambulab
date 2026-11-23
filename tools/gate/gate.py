#!/usr/bin/env python3
"""The print gate. Rules live in the plugin (rules/*.json); a project keeps only its own settings (gate/settings.json)
and the results of runs on its parts (gate/results/). Every check is deterministic: Fusion is read, Python judges.
The model only decides how to fix a finding, never how to find it.

    python3 <plugin>/tools/gate/gate.py check [--project DIR] [PART ...]      the heat-set gate on the printed parts
    python3 <plugin>/tools/gate/gate.py rules [--project DIR]                 the rules in force here (plugin + settings)

DIR: the current folder or the nearest one above it with fusion/parts.json (the parts and the Fusion document, as for
tools/export.py). gate/settings.json (optional):

    {"heatset": {"kinds": {"M3x4x5": {"pocket": {"hole": {"d": 3.65}}}}},   merged over rules/heatset.json, key by key
     "skip": ["part name", ...]}                                             parts the gate does not check (say why in "why")

Exit: 0 every checked part passes, 1 findings (each with what was measured, what is expected and how to fix it),
4 the gate could not run (Fusion does not answer, a part was not found). Results: gate/results/heatset/<part>.json,
one per part, with the document version, the body's faces/volume/area, the rules and settings hashes and the verdict:
a part counts as checked only while its file says pass and its body is still the one measured."""
import argparse, copy, hashlib, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__)); PLUGIN = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(PLUGIN, 'tools', 'fusion'))
import heatset_core  # noqa: E402
import fusion_rpc  # noqa: E402


def text(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def plugin_version():
    with open(os.path.join(PLUGIN, '.claude-plugin', 'plugin.json'), encoding='utf-8') as f:
        return json.load(f)['version']


def find_project(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, 'fusion', 'parts.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            sys.exit('no fusion/parts.json in %s or above it: pass --project' % start)
        d = up


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def load(root):
    with open(os.path.join(PLUGIN, 'rules', 'heatset.json'), encoding='utf-8') as f:
        rules = json.load(f)
    sp = os.path.join(root, 'gate', 'settings.json')
    settings = json.loads(text(sp)) if os.path.exists(sp) else {}
    with open(os.path.join(root, 'fusion', 'parts.json'), encoding='utf-8') as f:
        parts = json.load(f)
    return rules, settings, merge(rules, settings.get('heatset')), parts


def group_lines(findings, limit=3):
    """One line per (part, code, measured) with how many and the first places."""
    groups = {}
    for f in findings:
        groups.setdefault((f['part'], f['code'], f['measured']), []).append(f)
    for (part, code, measured), fs in sorted(groups.items()):
        f = fs[0]
        where = '; '.join('at (%s) out (%s)' % (', '.join('%.2f' % x for x in g['at']), ', '.join('%.3g' % x for x in g['out']))
                          for g in fs[:limit] if g.get('at')) + (' ... (%d more in the result file)' % (len(fs) - limit) if len(fs) > limit else '')
        yield '%s  %-22s x%-3d %s\n      expected: %s\n      fix:      %s\n      %s%s' % (
            part, code, len(fs), measured, f['expected'], f['fix'], where,
            ('\n      profile:  ' + f['profile']) if f.get('profile') else '')


def check(root, names, as_json=False):
    rules, settings, merged, pcfg = load(root)
    skip = set(settings.get('skip') or [])
    allp = pcfg['parts']
    unknown = [n for n in names if n not in allp]
    if unknown:
        print('not in fusion/parts.json: %s' % ', '.join(unknown)); return 4
    todo = [n for n in (names or list(allp)) if n not in skip]
    specs = []
    for n in todo:
        s = {k: v for k, v in allp[n].items() if k in ('component', 'path', 'body')}
        s['name'] = n
        if allp[n].get('doc') and allp[n]['doc'] != pcfg.get('doc'):
            print('%s is in document %s: the gate reads one document per run (%s); skipped' % (n, allp[n]['doc'], pcfg.get('doc')))
            continue
        specs.append(s)
    src = ''.join(text(os.path.join(HERE, f)) + '\n' for f in ('heatset_core.py', 'heatset_fusion.py'))
    t0 = time.time()
    try:
        facts = fusion_rpc.run_job(src, {'doc': pcfg.get('doc'), 'parts': specs, 'rules': merged}, write=False, timeout=600, name='heatset')
    except fusion_rpc.FusionError as e:
        print('the gate could not run: %s' % e); return 4
    t_fusion = time.time() - t0
    res = heatset_core.check(facts['parts'], facts['inserts'], merged)
    stamp = {'project': os.path.basename(root), 'gate': 'heatset', 'gate_version': plugin_version(), 'rules_sha': sha(rules), 'settings_sha': sha(settings.get('heatset') or {}),
             'doc': facts['doc'], 'doc_version': facts['version'], 'doc_modified': facts['modified'],
             'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    out_dir = os.path.join(root, 'gate', 'results', 'heatset'); os.makedirs(out_dir, exist_ok=True)
    by_part = {}
    for f in res['findings']:
        by_part.setdefault(f['part'], []).append(f)
    pockets_by = {}
    for p in res['pockets']:
        pockets_by[p['part']] = pockets_by.get(p['part'], 0) + 1
    failed = 0
    for p in facts['parts']:
        fs = by_part.get(p['name'], [])
        failed += bool(fs)
        rec = dict(stamp, part=p['name'], body={'faces': p['faces'], 'volume_mm3': p['volume'], 'area_mm2': p['area']},
                   verdict='fail' if fs else 'pass', pockets=pockets_by.get(p['name'], 0), findings=fs)
        with open(os.path.join(out_dir, p['name'] + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(rec, fh, indent=1, ensure_ascii=False); fh.write('\n')
    summary = dict(stamp, counts=res['counts'], parts=len(facts['parts']), failed=failed, errors=facts['errors'],
                   inserts_outside_printed_parts=res['outside'], fusion_s=round(t_fusion, 2), judge_s=round(time.time() - t0 - t_fusion, 3))
    with open(os.path.join(out_dir, '_summary.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False); fh.write('\n')
    if as_json:
        print(json.dumps({'summary': summary, 'findings': res['findings']}, indent=1)); return 1 if res['findings'] else (4 if facts['errors'] else 0)
    c = res['counts']
    print('heat-set gate %s on %s v%d%s: %d parts, %d pockets, %d inserts (%d in pockets, %d outside the printed parts)  [Fusion %.1f s, judge %.2f s]' % (
        stamp['gate_version'], facts['doc'], facts['version'], ' + unsaved changes' if facts['modified'] else '', len(facts['parts']),
        c['pockets'], c['inserts'], c['in_pockets'], c['outside_printed_parts'], t_fusion, summary['judge_s']))
    for e in facts['errors']:
        print('ERROR %s: %s' % (e['part'], e['error']))
    if res['outside']:
        by = {}
        for path in res['outside']:
            top = path.split('/')[0].split('+')[-1]
            by[top] = by.get(top, 0) + 1
        print('NOT CHECKED %d insert(s) sit in bodies that are not in fusion/parts.json: %s' % (
            len(res['outside']), ', '.join('%s x%d' % kv for kv in sorted(by.items(), key=lambda kv: -kv[1]))))
    for line in group_lines(res['findings']):
        print(line)
    print('PASS %d, FAIL %d of %d parts; %d findings. Results: %s' % (len(facts['parts']) - failed, failed, len(facts['parts']), len(res['findings']),
                                                                    os.path.relpath(out_dir, root)))
    if facts['errors']:
        return 4
    return 1 if res['findings'] else 0


INSERT_CODES = ('no_insert', 'not_golden', 'not_flush', 'upside_down', 'off_axis')


def read(root, names):
    """(facts, result, merged rules, parts config) of one read-only run, without writing results."""
    rules, settings, merged, pcfg = load(root)
    specs = [dict({k: v for k, v in pcfg['parts'][n].items() if k in ('component', 'path', 'body')}, name=n) for n in names]
    src = ''.join(text(os.path.join(HERE, f)) + '\n' for f in ('heatset_core.py', 'heatset_fusion.py'))
    facts = fusion_rpc.run_job(src, {'doc': pcfg.get('doc'), 'parts': specs, 'rules': merged}, write=False, timeout=600, name='heatset')
    return facts, heatset_core.check(facts['parts'], facts['inserts'], merged), merged, pcfg


def fix_inserts(root, names, component=None, dry=False):
    """Put the golden insert into every pocket of the parts that has none, and replace every insert that is not the
    golden one, not flush, upside down or off its axis, by the golden one on the relief floor. The pocket itself is not
    touched: a pocket that differs from the table keeps its findings (insert_pockets.py cuts pockets)."""
    if not names:
        print('name the parts to fix (gate.py fix-inserts PART ...)'); return 4
    try:
        facts, res, merged, pcfg = read(root, names)
    except fusion_rpc.FusionError as e:
        print('the gate could not run: %s' % e); return 4
    place, replace = {}, []
    codes = {}
    for f in res['findings']:
        if f['code'] in INSERT_CODES:
            codes.setdefault((f['part'], tuple(f['at'])), set()).add(f['code'])
    for p in res['pockets']:
        if p['part'] not in names:
            continue
        floor = [p['base'][i] + p['a'][i] * p['t_floor'] for i in range(3)]
        out = [-x * p['sign'] for x in p['a']]
        cs = codes.get((p['part'], tuple(heatset_core._at(p))), set())
        if not p['inserts']:
            place.setdefault(p['kind'], []).append({'floor': floor, 'out': out, 'part': p['part']})
        elif cs:
            replace.append((p['kind'], {'path': p['inserts'][0]['ins']['path'], 'floor': floor, 'out': out, 'why': sorted(cs)}))
    for kind, items in place.items():
        print('place   %d golden %s insert(s): %s' % (len(items), kind, ', '.join(sorted({i['part'] for i in items}))))
    for kind, it in replace:
        print('replace %s (%s)' % (it['path'], ', '.join(it['why'])))
    if dry or not (place or replace):
        print('nothing to do' if not (place or replace) else '(dry run: nothing changed)'); return 0
    src = ''.join(text(os.path.join(HERE, f)) + '\n' for f in ('heatset_core.py', 'heatset_fusion.py', 'golden.py'))
    comp = component or 'Heat-set inserts (golden)'
    for kind, items in place.items():
        r = fusion_rpc.run_job(src, {'action': 'place', 'doc': pcfg.get('doc'), 'component': comp, 'kind': merged['kinds'][kind],
                                     'items': [{'floor': i['floor'], 'out': i['out']} for i in items]}, write=True, timeout=300, name='golden')
        print('placed %d in component "%s"' % (len(r['made']), r['component']))
    by_kind = {}
    for kind, it in replace:
        by_kind.setdefault(kind, []).append(it)
    for kind, items in by_kind.items():
        r = fusion_rpc.run_job(src, {'action': 'replace', 'doc': pcfg.get('doc'), 'kind': merged['kinds'][kind], 'items': items}, write=True, timeout=300, name='golden')
        print('replaced %d' % len(r['replaced']))
    print('now run: gate.py check %s' % ' '.join(names))
    return 0


def main():
    a = argparse.ArgumentParser(description='the print gate')
    a.add_argument('cmd', choices=['check', 'rules', 'fix-inserts']); a.add_argument('parts', nargs='*'); a.add_argument('--project')
    a.add_argument('--json', action='store_true'); a.add_argument('--component'); a.add_argument('--dry-run', action='store_true')
    a = a.parse_args()
    root = os.path.abspath(a.project) if a.project else find_project(os.getcwd())
    if a.cmd == 'rules':
        rules, settings, merged, _ = load(root)
        print(json.dumps(merged, indent=1, ensure_ascii=False)); return 0
    if a.cmd == 'fix-inserts':
        return fix_inserts(root, a.parts, a.component, a.dry_run)
    return check(root, a.parts, a.json)


if __name__ == '__main__':
    sys.exit(main())
