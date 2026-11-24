import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ovh_audit import summarize, FORBIDDEN
from ovh_audit import HERE
d = json.load(open(os.path.join(HERE, 'audit.json')))
codes = ['overhang', 'long_bridge', 'round_hole', 'shallow_ridge', 'heatset_ceiling', 'bridge']
print('%-28s %5s %6s ' % ('part', 'zrot', 'open') + ' '.join('%6s' % c[:6] for c in codes) + '  forb_mm2  near45  ridge_mm pure_ridges(mm) small')
tot = 0
for n, p in d.items():
    if 'regions' not in p:
        print(n, p.get('error')); continue
    c, f, near = summarize(p, True)
    rl = sum(r['length_mm'] for r in p['regions'] if r['code'] == 'shallow_ridge')
    pr = [r for r in p['regions'] if r['code'] == 'shallow_ridge' and not r['on_overhang_faces']]
    prs = '%d(%.0f)' % (len(pr), sum(r['length_mm'] for r in pr))
    tot += f
    print('%-28s %5.0f %6d ' % (n[:28], p['z_rot_deg'], p['open_edges']) + ' '.join('%6d' % c.get(k, 0) for k in codes) + '  %8.1f %7.1f %8.1f %15s %5d' % (f, near, rl, prs, p['small_regions']))
print('total forbidden area', round(tot, 1), 'parts', len(d))
