"""Judge the felt profiles on their own (no gate code) and compare, pocket by pocket, with the gate's findings."""
import json, glob, math, collections, sys, os
M = None
R = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
rows = json.load(open(os.path.join(R, 'gate', 'results', 'heatset', '_audit_rows.json')))
gate = {}
for f in glob.glob(R + '/gate/results/heatset/*.json'):
    if os.path.basename(f).startswith('_'): continue
    r = json.load(open(f))
    for p in r['pocket_list']:
        gate[(r['part'], tuple(p['at']))] = {'codes': set(), 'pl': p}
    for x in r['findings']:
        k = (x['part'], tuple(x['at']))
        if k in gate: gate[k]['codes'].add(x['code'])
RELIEFS, LEAD, HOLE, NEED, TD, TL = (7.5, 6.5, 5.9), 4.4, 3.6, 6.5, 0.06, 0.075
GOLD_VOL = math.pi * (2.1**2 * 1.0 + 2.5**2 * 3.0 - 1.5**2 * 4.0)
IGNORE = {'off_axis', 'not_a_pocket'}          # not felt here (the second is the gate's label, its parts are compared)


def runs(prof):
    """[(D, start, end, open)] from depth 0 on; D None = solid; D 'out' = no plastic in any direction."""
    out = []
    for s, ds, _ in prof:
        if s < 0.05: continue                           # the mouth plane itself (and the 0.03 next to it) is no depth of the pocket
        if ds == 'solid': D, op = 'solid', False
        else:
            got = [x for x in ds if x is not None]
            D = min(got) if got else 'out'; op = len(got) < len(ds)
        if out and ((D == out[-1][0]) if isinstance(D, str) or isinstance(out[-1][0], str) else abs(D - out[-1][0]) < 0.04):
            out[-1][2] = s; out[-1][3] = out[-1][3] or op; out[-1][4].append(D)
        else:
            out.append([D, s, s, op, [D]])
    res = []
    for D, a, b, op, ds in out:
        if not isinstance(D, str): D = sorted(ds)[len(ds) // 2]
        res.append((D, a, b + 0.03, op))
    return res


def near(d, want): return not isinstance(d, str) and abs(d - want) <= TD


def judge(row, pl):
    rr = runs(row['prof'])
    codes = set()
    i = 0
    while i < len(rr) and (rr[i][0] == 'out' or (not isinstance(rr[i][0], str) and rr[i][0] > 7.6)):
        i += 1
    r = rr[i:]
    if not r: return {'no_profile'}, rr, 0
    floor = r[0][1]
    if any(near(r[0][0], x) for x in RELIEFS):
        if abs((r[0][2] - r[0][1]) - 0.54) > TL: codes.add('relief_depth')
        floor = r[0][2]; j = 1
    else:
        j = 0
        if not isinstance(r[0][0], str) and r[0][0] > LEAD + TD: codes.add('relief_d'); floor = r[0][2]; j = 1
        else: codes.add('relief_missing')
    lead = r[j] if j < len(r) else None
    if lead and near(lead[0], LEAD):
        if abs((lead[2] - lead[1]) - 1.5) > TL: codes.add('lead_in_depth')
        k = j + 1
    elif lead and near(lead[0], HOLE):
        codes.add('lead_in_missing'); k = j
    else:
        codes.add('lead_in_d'); k = j + 1
    hole = r[k] if k < len(r) else None
    if hole is None or hole[0] in ('solid',):
        codes.add('hole_missing')
    elif hole[0] == 'out':
        pass                                            # runs out of the part right away: through
    elif not near(hole[0], HOLE):
        codes.add('hole_d')
    else:
        reach = hole[2] - floor
        nxt = r[k + 1][0] if k + 1 < len(r) else 'out'
        if reach < NEED - TL and nxt != 'out' and not (hole[2] >= 13.9):
            codes.add('hole_short')
    lim = floor + NEED
    if any(op and a < lim - 0.1 and not isinstance(D, str) and D < 7.6 for D, a, b, op in r):
        codes.add('open_wall')
    ins = [x for x in row['inserts'] if x['vol'] < 1000]          # a printed post named 'M3 insert both ends' is no insert
    if not ins: codes.add('no_insert')
    if len(ins) > 1: codes.add('two_inserts')
    if ins:
        x = ins[0]
        shape_ok = abs(x['len'] - 4.0) < 0.06 and all(abs(w - 5.0) < 0.06 for w in x['wid']) and abs(x['vol'] - GOLD_VOL) < 0.6 and x['bore_air']
        if not shape_ok or x['top_r23'] == x['bot_r23']: codes.add('not_golden')
        elif not x['top_r23'] and x['bot_r23']: codes.add('upside_down')
        if abs(x['top'] - floor) > 0.06 + 0.03: codes.add('not_flush')
    return codes, r, floor


agree, diff = 0, []
per = collections.Counter()
for row in rows:
    k = (row['part'], tuple(row['at']))
    g = gate.get(k)
    mine, r, floor = judge(row, g['pl'])
    theirs = g['codes'] - IGNORE
    if mine == theirs: agree += 1
    else: diff.append((row['part'], row['at'], sorted(theirs - mine), sorted(mine - theirs), g['pl']['profile'],
                       ' -> '.join('%s %.2f-%.2f%s' % (D if isinstance(D, str) else 'D%.2f' % D, a, b, ' open' if op else '') for D, a, b, op in r[:6]),
                       [(x['path'][-40:], x['top'], x['len'], x['vol']) for x in row['inserts']]))
    for c in theirs: per[c] += 1
print('pockets compared %d: agree %d, differ %d' % (len(rows), agree, len(diff)))
print('gate codes on these pockets:', dict(per))
for d in diff: print('\n%s %s\n  gate only: %s   audit only: %s\n  gate profile:  %s\n  felt profile:  %s\n  inserts: %s' % d)
