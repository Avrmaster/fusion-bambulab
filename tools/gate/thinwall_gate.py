"""Host side of the thin-wall gate: what `gate.py check --gate thinwall [PART ...]` runs.

One Fusion call reads the meshes of the printed bodies (thinwall_fusion.py); the judging is thinwall_core.judge(), one
process per part. The limits of a part come from the Bambu projects fusion/parts.json puts it in ("3mf": [{"file",
"object", "face"}]): the outer wall line width, the layer height and the wall generator of the project, or of the
object where the project sets them per object. Results: gate/results/thinwall/<part>.json (one per part) and
_summary.json. Exit 0 every checked part passes, 1 findings, 4 the gate could not run."""
import json, os, re, sys, time, zipfile
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import thinwall_core  # noqa: E402


def _num(v):
    if isinstance(v, list):
        v = v[0]
    v = str(v).strip()
    return float(v[:-1]) / 100 if v.endswith('%') else float(v)


def project_settings(path):
    """{"global": {key: value}, "objects": {object name: {key: value}}} of a Bambu .3mf (project and per-object
    settings; only the keys the gate uses)."""
    keys = ('outer_wall_line_width', 'line_width', 'layer_height', 'wall_generator', 'nozzle_diameter')
    with zipfile.ZipFile(path) as z:
        ps = json.loads(z.read('Metadata/project_settings.config'))
        ms = z.read('Metadata/model_settings.config').decode('utf-8', 'replace')
    g = {k: ps.get(k) for k in keys}
    objs = {}
    for m in re.finditer(r'<object id="\d+">(.*?)</object>', ms, re.S):
        body = m.group(1).split('<part ')[0]
        kv = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', body))
        if 'name' in kv:
            objs[kv['name']] = {k: kv[k] for k in keys if k in kv}
    return {'global': g, 'objects': objs}


def part_projects(root, spec, cache):
    """[{"project", "object", "line_mm", "arachne", "layer_mm", "found"}] for the .3mf entries of one part."""
    out = []
    for e in spec.get('3mf') or []:
        path = os.path.join(root, e['file'])
        if path not in cache:
            cache[path] = project_settings(path) if os.path.exists(path) else None
        s = cache[path]
        if s is None:
            out.append({'project': e['file'], 'object': e.get('object'), 'found': False, 'line_mm': None}); continue
        o = s['objects'].get(e.get('object')) or {}
        g = s['global']
        line = o.get('outer_wall_line_width') or g.get('outer_wall_line_width') or o.get('line_width') or g.get('line_width')
        line = _num(line)
        if line < 0.1:                                  # a share of the nozzle
            line = line * _num(g['nozzle_diameter'])
        gen = o.get('wall_generator') or g.get('wall_generator') or 'classic'
        out.append({'project': e['file'], 'object': e.get('object'), 'found': e.get('object') in s['objects'], 'line_mm': round(line, 4),
                    'arachne': gen == 'arachne', 'layer_mm': _num(o.get('layer_height') or g.get('layer_height'))})
    return out


def _judge(args):
    part, rules, projects, face, accepted = args
    t = time.time()
    r = thinwall_core.judge(part, rules, projects, face, accepted)
    r['judge_s'] = round(time.time() - t, 2)
    return part['name'], r


def check(root, names, gate, as_json=False, facts=None):
    """gate: the gate module (gate.py), for its loaders, hashes and Fusion runner. facts: a thinwall_fusion.py result
    read before (the judge alone runs again; for tests and for tuning the rules without Fusion)."""
    import fusion_rpc
    rules_all = gate.load_rules('thinwall')
    sp = os.path.join(root, 'gate', 'settings.json')
    settings = json.loads(gate.text(sp)) if os.path.exists(sp) else {}
    mine = settings.get('thinwall') or {}
    rules = gate.merge(rules_all, {k: v for k, v in mine.items() if k != 'accept'})
    accept = mine.get('accept') or []
    with open(os.path.join(root, 'fusion', 'parts.json'), encoding='utf-8') as f:
        pcfg = json.load(f)
    allp = pcfg['parts']
    unknown = [n for n in names if n not in allp]
    if unknown:
        print('not in fusion/parts.json: %s' % ', '.join(unknown)); return 4
    skip = set(settings.get('skip') or [])
    todo = [n for n in (names or list(allp)) if n not in skip]
    specs, cache, projects, problems = [], {}, {}, []
    for n in todo:
        if allp[n].get('doc') and allp[n]['doc'] != pcfg.get('doc'):
            print('%s is in document %s: the gate reads one document per run (%s); skipped' % (n, allp[n]['doc'], pcfg.get('doc'))); continue
        specs.append(dict({k: v for k, v in allp[n].items() if k in ('component', 'path', 'body')}, name=n))
        projects[n] = part_projects(root, allp[n], cache)
        for pr in projects[n]:
            if not pr['found']:
                problems.append('%s: object %r not in %s (limits from the project only)' % (n, pr['object'], pr['project']))
        if not projects[n]:
            problems.append('%s: no .3mf in fusion/parts.json: judged against the rulebook wall only, no line limits' % n)
    t0 = time.time()
    try:
        facts = facts or fusion_rpc.run_job(gate.text(os.path.join(HERE, 'thinwall_fusion.py')), {'doc': pcfg.get('doc'), 'parts': specs},
                                            write=False, timeout=180, name='thinwall')
    except fusion_rpc.FusionError as e:
        print('the gate could not run: %s' % e); return 4
    t_fusion = time.time() - t0
    jobs = [(p, rules, [pr for pr in projects[p['name']] if pr.get('line_mm')], (allp[p['name']].get('3mf') or [{}])[0].get('face'),
             [a for a in accept if a.get('part') == p['name']]) for p in facts['parts']]
    t1 = time.time()
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
        results = dict(ex.map(_judge, sorted(jobs, key=lambda j: -len(j[0]['faces']))))
    t_judge = time.time() - t1
    stamp = {'project': os.path.basename(root), 'gate': 'thinwall', 'gate_version': gate.plugin_version(), 'rules_sha': gate.sha(rules_all),
             'settings_sha': gate.sha(mine), 'doc': facts['doc'], 'doc_version': facts['version'], 'doc_modified': facts['modified'],
             'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    out_dir = os.path.join(root, 'gate', 'results', 'thinwall'); os.makedirs(out_dir, exist_ok=True)
    failed, allf = 0, []
    for p in facts['parts']:
        r = results[p['name']]
        fs = r['findings']; allf.extend(fs)
        failed += bool(fs)
        rec = dict(stamp, part=p['name'], body={'faces': p['face_count'], 'volume_mm3': p['volume'], 'area_mm2': p['area']},
                   projects=projects[p['name']], limits={k: r['limits'][k] for k in ('min_wall', 'two_lines', 'one_line', 'project', 'arachne', 'line_mm', 'layer_mm')},
                   verdict='fail' if fs else 'pass', counts=r['counts'], findings=fs, accepted=r['accepted'])
        with open(os.path.join(out_dir, p['name'] + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(rec, fh, indent=1, ensure_ascii=False); fh.write('\n')
    codes = {}
    for f in allf:
        codes[f['code']] = codes.get(f['code'], 0) + 1
    exempt = {k: sum(results[p['name']]['counts'][k] for p in facts['parts']) for k in ('corner', 'wedge', 'teeth', 'sacrificial_layer')}
    summary = dict(stamp, parts=len(facts['parts']), failed=failed, findings=len(allf), codes=codes, exempt_samples=exempt,
                   samples=sum(results[p['name']]['counts']['samples'] for p in facts['parts']), errors=facts['errors'], problems=problems,
                   fusion_s=round(t_fusion, 2), judge_s=round(t_judge, 2))
    with open(os.path.join(out_dir, '_summary.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False); fh.write('\n')
    if as_json:
        print(json.dumps({'summary': summary, 'findings': allf}, indent=1))
    else:
        print('thin-wall gate %s on %s v%d%s: %d parts, %d samples  [Fusion %.1f s, judge %.1f s]' % (
            stamp['gate_version'], facts['doc'], facts['version'], ' + unsaved changes' if facts['modified'] else '', len(facts['parts']),
            summary['samples'], t_fusion, t_judge))
        print('not reported, by rule (samples): %s' % ', '.join('%s %d' % kv for kv in exempt.items()))
        for e in facts['errors']:
            print('ERROR %s: %s' % (e['part'], e['error']))
        for pr in problems:
            print('NOTE %s' % pr)
        for p in facts['parts']:
            r = results[p['name']]
            for f in r['findings']:
                num = '      ' + ' ' * 8 if f['code'] == 'faces_cross' else '%6.3f < %.3f' % (f['thickness_mm'], f['limit_mm'])
                print('%-28s %-16s %s at (%s)  %s' % (p['name'], f['code'], num, ', '.join('%.2f' % x for x in f['at']), f['measured']))
            for f in r['accepted']:
                print('%-28s accepted         %6.3f at (%s): %s' % (p['name'], f['thickness_mm'], ', '.join('%.2f' % x for x in f['at']), f['accepted']))
        print('PASS %d, FAIL %d of %d parts; %d findings (%s). Results: %s' % (
            len(facts['parts']) - failed, failed, len(facts['parts']), len(allf), ', '.join('%s %d' % kv for kv in sorted(codes.items())),
            os.path.relpath(out_dir, root)))
    if facts['errors']:
        return 4
    return 1 if allf else 0
