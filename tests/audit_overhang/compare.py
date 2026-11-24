"""Gate regions vs the independent audit, region by region, in the audit's frame (the placed mesh, from its box min).
Run from the project folder after gate.py overhang and ovh_audit.py:  python3 <plugin>/tests/audit_overhang/compare.py [PART ...]
NORIDGE=1 hides ridges; NEAR=mm (2.5) is how far apart two regions may be and still match. This file only maps the
gate's results into the audit's frame (it uses the plugin's slicer frame for that); the audit's verdicts are its own."""
import json, os, sys
PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PLUGIN, 'tools', 'gate')); sys.path.insert(0, os.path.join(PLUGIN, 'tools', 'fusion'))
import overhang_slicer as sl

ROOT = os.path.abspath(os.environ.get('PROJECT', os.getcwd()))
A = json.load(open(os.path.join(os.environ.get('AUDIT_OUT', os.path.join(os.path.expanduser('~'), '.print-engineering', 'audit_overhang')), 'audit.json')))
P = json.load(open(os.path.join(ROOT, 'fusion', 'parts.json')))['parts']
NEAR = float(os.environ.get('NEAR', 2.5))
O3 = {}
rows, tot = [], {'match': 0, 'gate_only': 0, 'audit_only': 0}
only = sys.argv[1:]
for name, a in sorted(A.items()):
    if only and name not in only:
        continue
    t = P[name]['3mf'][0]
    f = os.path.join(ROOT, t['file'])
    if f not in O3:
        O3[f] = sl.read_3mf(f)[0]
    o3 = O3[f][t['object']]
    rec = json.load(open(os.path.join(ROOT, 'gate', 'results', 'overhang', name + '.json')))
    fr = sl.Frame(t['face'], o3, rec['box'], [[0, 0, 0], [1, 1, 1]])
    fr.t = [-o3['glo'][i] for i in range(3)]
    stale = any(abs(fr.size_model[i] - fr.size_mesh[i]) > 1.0 for i in range(3))
    G = [r for r in rec['findings'] + rec['allowed'] if r.get('box')]
    def gbox(r):
        lo, hi = r['box']
        pts = [fr.to_print([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]
    def near(b1, b2):
        return all(b1[0][i] - NEAR <= b2[1][i] and b2[0][i] - NEAR <= b1[1][i] for i in range(3))
    AR = [x for x in a['regions'] if x['code'] != 'bridge' and (x['code'] == 'shallow_ridge' or (x.get('area_mm2') or 0) >= 1.0) and not (x['code'] == 'shallow_ridge' and x.get('on_overhang_faces'))]
    gb = [gbox(r) for r in G]
    used_a = set()
    out = []
    for r, b in zip(G, gb):
        hits = [j for j, x in enumerate(AR) if near(b, x['box']) and (x['code'] == 'shallow_ridge') == (r['code'] == 'shallow_ridge')]
        used_a.update(hits)
        same = [AR[j]['code'] for j in hits]
        out.append(('G', r['code'], r['area_mm2'], r['slope_deg'], [round(v, 1) for v in fr.to_print(r['at'])], r.get('length_mm'), same))
    INFO = [(r, gbox(r)) for r in rec.get('info') or []]
    for j, x in enumerate(AR):
        if j not in used_a:
            inf = sorted({r['code'] for r, b in INFO if near(b, x['box'])})
            if inf and x['code'] in ('heatset_ceiling',) and 'bridge' in inf:
                tot['match'] += 1; continue                                  # both allow it: gate as a bridge
            out.append(('A', x['code'], x.get('area_mm2'), x.get('min_slope_deg', x.get('rise_deg')), [round(v, 1) for v in x['at']], x.get('length_mm'), inf))
    g_only = [o for o in out if o[0] == 'G' and not o[6]]
    a_only = [o for o in out if o[0] == 'A']
    tot['gate_only'] += len([o for o in g_only if o[1] != 'shallow_ridge']); tot['audit_only'] += len([o for o in a_only if o[1] != 'shallow_ridge'])
    tot['match'] += len([o for o in out if o[0] == 'G' and o[6]])
    code_diff = [o for o in out if o[0] == 'G' and o[6] and o[1] not in o[6]]
    print('== %s%s: gate %d regions, audit %d; gate only %d, audit only %d, code differs %d' % (
        name, ' (STALE MESH)' if stale else '', len(G), len(AR), len(g_only), len(a_only), len(code_diff)))
    for o in g_only + a_only + code_diff:
        if o[1] == 'shallow_ridge' and os.environ.get('NORIDGE'):
            continue
        print('   %s %-15s area %7.1f slope %5.1f at %s %s %s' % (o[0], o[1], o[2] or 0, o[3] or 0, o[4], ('len %.1f' % o[5]) if o[5] else '', o[6]))

print(tot)
