#!/usr/bin/env python3
"""Read a posted G-code file and report what it will do. Never changes the file.
    python3 nc_check.py job.nc [more.nc] [--x LO:HI --y LO:HI --z LO:HI] [--allow G53] [--json]

Reports, per file: the cutting box and the overall box (arcs included, in mm), first Z, tools, spindle speeds, feeds,
coolant, and a rough time. Exit code 1 when a file has an error:
  - G28 / G30 (go to a stored machine position: on a homed GRBL machine G28 G91 Z0 drives Z into the limit switch),
    G53 (machine coordinates, so the move ignores where Z0 is) and G92 (shifts the work zero) unless --allow names them
  - a rapid (G0) that moves X or Y while the tool is below Z0
  - a cutting move outside --x / --y / --z
Warnings: the first move goes sideways before any Z (it runs at whatever height the tool is at), no spindle start before
the first cut, coolant on (M7/M8), a rapid down below Z0 deeper than this file has cut."""
import argparse, json, math, re, sys

WORD = re.compile(r'([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))')
PLANES = {17: ('X', 'Y', 'I', 'J'), 18: ('Z', 'X', 'K', 'I'), 19: ('Y', 'Z', 'J', 'K')}


def strip(line):
    line = re.sub(r'\([^)]*\)', '', line.split(';', 1)[0])
    return line.upper().strip()


class Box:
    def __init__(self): self.lo, self.hi = {}, {}

    def add(self, axis, v):
        if v is None: return
        self.lo[axis] = min(self.lo.get(axis, v), v); self.hi[axis] = max(self.hi.get(axis, v), v)

    def get(self):
        return {a: [round(self.lo[a], 3), round(self.hi[a], 3)] for a in sorted(self.lo)}


def arc_extremes(a0, a1, cw):
    """Angles (multiples of 90 degrees) passed when sweeping from a0 to a1."""
    if cw: a0, a1 = a1, a0
    while a1 <= a0 + 1e-9: a1 += 2 * math.pi
    out, k = [], math.ceil(a0 / (math.pi / 2) - 1e-9)
    while k * math.pi / 2 < a1 - 1e-9:
        out.append(k * math.pi / 2); k += 1
    return out


def check(text, limits=None, allow=(), rapid=3000.0):
    pos = {'X': None, 'Y': None, 'Z': None}
    absolute, scale, motion, plane = True, 1.0, None, 17
    cut, every = Box(), Box()
    errors, warnings, tools, speeds, feeds, coolant = [], [], [], set(), set(), []
    feed, spindle_on, first_z, first_move_seen, seconds, deepest = None, False, None, False, 0.0, 0.0
    limits = limits or {}

    def where(n): return 'line %d' % n

    for n, raw in enumerate(text.splitlines(), 1):
        line = strip(raw)
        if not line or line.startswith('%'): continue
        words = [(w, float(v)) for w, v in WORD.findall(line)]
        gs = [int(v) if v == int(v) else v for w, v in words if w == 'G']
        ms = [int(v) for w, v in words if w == 'M']
        vals = {w: v for w, v in words if w not in 'GMN'}
        for g in gs:
            if g in (28, 30, 53, 92) and ('G%d' % g) not in allow:
                errors.append('%s: G%d (%s)' % (where(n), g, raw.strip()))
            if g == 90: absolute = True
            elif g == 91: absolute = False
            elif g == 20: scale = 25.4
            elif g == 21: scale = 1.0
            elif g in PLANES: plane = g
            elif g in (0, 1, 2, 3): motion = g
            elif g == 80: motion = None
        machine = 53 in gs or 28 in gs or 30 in gs
        for m in ms:
            if m in (3, 4): spindle_on = True
            elif m == 5: spindle_on = False
            elif m in (7, 8): coolant.append('M%d %s' % (m, where(n)))
        if 'T' in vals: tools.append(int(vals['T']))
        if 'S' in vals: speeds.add(vals['S'])
        if 'F' in vals:
            feed = vals['F'] * scale; feeds.add(round(feed, 1))
        target = dict(pos)
        moved = False
        for a in 'XYZ':
            if a in vals:
                v = vals[a] * scale
                target[a] = v if absolute or pos[a] is None else pos[a] + v
                moved = True
        if not moved or motion is None or 92 in gs: continue
        if machine:  # machine coordinates: position in work coordinates is unknown afterwards
            for a in 'XYZ':
                if a in vals: pos[a] = None
            continue
        if not first_move_seen:
            first_move_seen = True
            if pos['Z'] is None and target['Z'] is None:
                warnings.append('%s: the first move goes sideways at the current Z; raise Z before Start' % where(n))
        if first_z is None and target['Z'] is not None: first_z = target['Z']
        if motion == 0:
            below = pos['Z'] is not None and pos['Z'] < -1e-6
            sideways = any(target[a] != pos[a] for a in 'XY')
            if below and sideways: errors.append('%s: rapid sideways with the tool at Z%.3f' % (where(n), pos['Z']))
            elif target['Z'] is not None and target['Z'] < min(deepest, 0) - 1e-6 and (pos['Z'] is None or target['Z'] < pos['Z']):
                warnings.append('%s: rapid down to Z%.3f, below anything this file cut; safe only if an earlier program cut there'
                                % (where(n), target['Z']))
        elif not spindle_on:
            warnings.append('%s: cutting move with the spindle off' % where(n)); spindle_on = True
        boxes = (every,) if motion == 0 else (every, cut)
        for a in 'XYZ':
            every.add(a, pos[a])
            for b in boxes: b.add(a, target[a])
        if motion != 0 and target['Z'] is not None: deepest = min(deepest, target['Z'])
        dist = math.sqrt(sum((target[a] - pos[a]) ** 2 for a in 'XYZ' if target[a] is not None and pos[a] is not None))
        if motion in (2, 3):
            u, v, iu, iv = PLANES[plane]
            w = ({'X', 'Y', 'Z'} - {u, v}).pop()
            if pos[u] is not None and pos[v] is not None:
                if 'R' in vals:  # endpoints only; Fusion posts use I/J/K
                    dist = max(dist, abs(vals['R'] * scale) * math.pi / 2)
                else:
                    cu, cv = pos[u] + vals.get(iu, 0) * scale, pos[v] + vals.get(iv, 0) * scale
                    r = math.hypot(pos[u] - cu, pos[v] - cv)
                    a0 = math.atan2(pos[v] - cv, pos[u] - cu); a1 = math.atan2(target[v] - cv, target[u] - cu)
                    full = abs(target[u] - pos[u]) < 1e-6 and abs(target[v] - pos[v]) < 1e-6
                    angs = [0, math.pi / 2, math.pi, 3 * math.pi / 2] if full else arc_extremes(a0, a1, motion == 2)
                    for t in angs:
                        for b in boxes:
                            b.add(u, cu + r * math.cos(t)); b.add(v, cv + r * math.sin(t))
                    sweep = 2 * math.pi if full else (a0 - a1 if motion == 2 else a1 - a0) % (2 * math.pi)
                    dist = math.hypot(r * sweep, abs(target[w] - pos[w]) if pos[w] is not None and target[w] is not None else 0)
        rate = rapid if motion == 0 else (feed or rapid)
        seconds += dist / rate * 60
        pos = target

    box = cut.get()
    for a, (lo, hi) in limits.items():
        if a not in box: continue
        if lo is not None and box[a][0] < lo - 1e-6: errors.append('cut goes to %s%.3f, below the limit %s' % (a, box[a][0], lo))
        if hi is not None and box[a][1] > hi + 1e-6: errors.append('cut goes to %s%.3f, above the limit %s' % (a, box[a][1], hi))
    if coolant: warnings.append('coolant on: ' + ', '.join(coolant[:3]))
    return {'cut': box, 'all': every.get(), 'first_z': first_z, 'tools': sorted(set(tools)),
            'speeds': sorted(speeds), 'feeds': sorted(feeds), 'minutes': round(seconds / 60, 1),
            'errors': errors, 'warnings': warnings}


def summary(r):
    c = r['cut'].get('Z')
    return 'Z %s to %s, tools %s, %s min' % (c[0] if c else '?', r['all'].get('Z', ['?', '?'])[1],
                                              ','.join('T%d' % t for t in r['tools']) or '-', r['minutes'])


def span(s):
    lo, _, hi = s.partition(':')
    return (float(lo) if lo else None, float(hi) if hi else None)


def main():
    a = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    a.add_argument('files', nargs='+')
    for ax in 'xyz': a.add_argument('--' + ax, type=span, metavar='LO:HI', help='allowed cutting range, either end may be empty')
    a.add_argument('--allow', action='append', default=[], help='G28, G30, G53 or G92 to accept')
    a.add_argument('--rapid', type=float, default=3000.0, help='rapid rate for the time estimate, mm/min')
    a.add_argument('--json', action='store_true')
    o = a.parse_args()
    limits = {ax.upper(): getattr(o, ax) for ax in 'xyz' if getattr(o, ax)}
    bad, out = False, {}
    for f in o.files:
        r = check(open(f, encoding='utf-8', errors='replace').read(), limits, [x.upper() for x in o.allow], o.rapid)
        out[f] = r; bad |= bool(r['errors'])
        if o.json: continue
        print('%s\n  cut   %s\n  all   %s\n  first Z %s · tools %s · S %s · F %s · ≈ %s min' % (
            f, r['cut'], r['all'], r['first_z'], r['tools'], r['speeds'], r['feeds'], r['minutes']))
        for e in r['errors']: print('  ERROR', e)
        for w in r['warnings']: print('  warn ', w)
        print('  ' + ('FAIL' if r['errors'] else 'OK'))
    if o.json: print(json.dumps(out, indent=1, ensure_ascii=False))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
