"""The overhang gate (gate.py overhang | overhang-slice). Rules: rules/overhang.json, merged with the project's
gate/settings.json "overhang". The bed face of a part is the "face" of its 3mf entries in fusion/parts.json, the one
source of which side prints down.

    gate.py overhang [PART ...]          geometry, one Fusion call, seconds: gate/results/overhang/<part>.json
    gate.py overhang-slice [PART ...]    slice COPIES of the parts' .3mf projects with the Bambu Studio CLI (minutes: run it
                                         in the background) and compare where the slicer puts support with the regions of
                                         the last geometry run: gate/results/overhang/<part>.json "slicer", _slicer.json

Exit: 0 every checked part passes, 1 findings, 4 the gate could not run."""
import json, os, sys, time
import overhang_core, heatset_core
import fusion_rpc

HERE = os.path.dirname(os.path.abspath(__file__))


def load(root):
    import gate
    with open(os.path.join(gate.PLUGIN, 'rules', 'overhang.json'), encoding='utf-8') as f:
        rules = json.load(f)
    hs_rules, settings, hs_merged, parts = gate.load(root)
    return rules, settings, gate.merge(rules, settings.get('overhang')), hs_merged, parts


def specs_of(pcfg, names, skip):
    specs, problems = [], {}
    for n in names or list(pcfg['parts']):
        if n in skip:
            continue
        e = pcfg['parts'][n]
        s = {k: v for k, v in e.items() if k in ('component', 'path', 'body')}
        s['name'] = n
        face, problem = overhang_core.bed_faces(e)
        if problem:
            problems[n] = {'problem': problem, 'measured': 'bed faces in fusion/parts.json: %s' % (
                sorted({t.get('face') for t in e.get('3mf') or []}) or 'none')}
        if face:
            s['face'] = face; s['down'] = overhang_core.AXES[face]
        specs.append(s)
    return specs, problems


def read(root, names):
    import gate
    rules, settings, merged, hs_merged, pcfg = load(root)
    skip = set(settings.get('skip') or [])
    unknown = [n for n in names if n not in pcfg['parts']]
    if unknown:
        raise KeyError('not in fusion/parts.json: %s' % ', '.join(unknown))
    specs, problems = specs_of(pcfg, names, skip)
    src = ''.join(gate.text(os.path.join(HERE, f)) + '\n' for f in ('heatset_core.py', 'heatset_fusion.py', 'overhang_core.py', 'overhang_fusion.py'))
    facts = fusion_rpc.run_job(src, {'doc': pcfg.get('doc'), 'parts': specs, 'rules': merged, 'heatset_rules': hs_merged},
                               write=False, timeout=900, name='overhang')
    hs = heatset_core.check(facts['heatset']['parts'], facts['heatset']['inserts'], hs_merged)
    pockets = {}
    for p in hs['pockets']:
        pockets.setdefault(p['part'], []).append(p)
    seen = {p['name'] for p in facts['parts']}
    for n, pr in problems.items():
        if n not in seen:
            facts['parts'].append(dict(name=n, **pr))
        else:
            next(p for p in facts['parts'] if p['name'] == n).update(pr)
    return facts, pockets, rules, settings, merged, pcfg


def check(root, names, as_json=False):
    import gate
    t0 = time.time()
    try:
        facts, pockets, rules, settings, merged, pcfg = read(root, names)
    except KeyError as e:
        print(e); return 4
    except fusion_rpc.FusionError as e:
        print('the gate could not run: %s' % e); return 4
    t_f = time.time() - t0
    res = overhang_core.check(facts['parts'], merged, pockets)
    stamp = {'project': os.path.basename(root), 'gate': 'overhang', 'gate_version': gate.plugin_version(), 'rules_sha': gate.sha(rules),
             'settings_sha': gate.sha(settings.get('overhang') or {}), 'doc': facts['doc'], 'doc_version': facts['version'],
             'doc_modified': facts['modified'], 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    out_dir = os.path.join(root, 'gate', 'results', 'overhang'); os.makedirs(out_dir, exist_ok=True)
    by = {}
    for f in res['findings']:
        by.setdefault(f['part'], []).append(f)
    alw = {}
    for f in res['allowed']:
        alw.setdefault(f['part'], []).append(f)
    failed = 0
    for p in facts['parts']:
        fs = by.get(p['name'], [])
        failed += bool(fs)
        path = os.path.join(out_dir, p['name'] + '.json')
        old = {}
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                old = json.load(fh)
        rec = dict(stamp, part=p['name'], bed_face=p.get('face'), down=p.get('down'), box=p.get('box'),
                   body={'faces': p.get('n_faces'), 'volume_mm3': p.get('volume'), 'area_mm2': p.get('area')} if p.get('faces') is not None else None,
                   verdict='fail' if fs else 'pass', findings=fs, allowed=alw.get(p['name'], []), small_regions=res['small'].get(p['name'], 0),
                   heatset_pockets=len(pockets.get(p['name'], [])), fusion_s=p.get('s'), info=res['info'].get(p['name'], []))
        if old.get('slicer') and old.get('body') == rec['body']:
            rec['slicer'] = old['slicer']                       # the slicer comparison of the same body stays
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(rec, fh, indent=1, ensure_ascii=False); fh.write('\n')
    counts = {}
    for f in res['findings'] + res['allowed']:
        counts[f['code']] = counts.get(f['code'], 0) + 1
    summary = dict(stamp, parts=len(facts['parts']), failed=failed, counts=counts, errors=facts['errors'],
                   fusion_s=round(t_f, 2), judge_s=round(time.time() - t0 - t_f, 2))
    with open(os.path.join(out_dir, '_summary.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False); fh.write('\n')
    if as_json:
        print(json.dumps({'summary': summary, 'findings': res['findings'], 'allowed': res['allowed']}, indent=1))
    else:
        print('overhang gate %s on %s v%d%s: %d parts  [Fusion %.1f s, judge %.2f s]' % (
            stamp['gate_version'], facts['doc'], facts['version'], ' + unsaved changes' if facts['modified'] else '',
            len(facts['parts']), t_f, summary['judge_s']))
        for e in facts['errors']:
            print('ERROR %s: %s' % (e['part'], e['error']))
        for f in sorted(res['findings'], key=lambda f: (f['part'], f['code'], -f.get('area_mm2', 0))):
            print('%s  %-15s %s%s\n      fix: %s' % (f['part'], f['code'], f['measured'],
                                                   ('  at (%s)' % ', '.join('%.1f' % x for x in f['at'])) if f.get('at') else '', f['fix']))
        for part, fs in sorted(alw.items()):
            print('%s  allowed: %d heat-set pocket ceiling(s), %.1f mm2' % (part, len(fs), sum(f['area_mm2'] for f in fs)))
        print('PASS %d, FAIL %d of %d parts; %d findings, %d allowed. Results: %s' % (
            len(facts['parts']) - failed, failed, len(facts['parts']), len(res['findings']), len(res['allowed']), os.path.relpath(out_dir, root)))
    if facts['errors']:
        return 4
    return 1 if res['findings'] else 0


def slice_cmd(root, names, keep=None):
    import overhang_slicer
    return overhang_slicer.run(root, names, keep)
