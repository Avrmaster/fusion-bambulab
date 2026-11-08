#!/usr/bin/env python3
"""Slice a project with the Bambu Studio CLI, on a copy, and print time and grams per plate. Exit 1 if slicing fails.
    python3 bambulab/tools/slice_check.py <project.3mf> [--plate N] [--keep <dir>]
For PLA plates it also estimates each layer's time from the G-code and warns about layers much faster or slower than the
typical layer: on PLA the cooling difference shows as a band on the outer walls (PETG and nylon blends don't show it).
It prints the height of every print pause per plate (the Z of the layer printed right after it), and names the objects Bambu
reports when slicing stops on an object conflict (an object over a keep-out area such as the P2S clumping-detection strip).
Safe while the GUI is open. It proves the project slices, NOT that the GUI opens it: the GUI validates project_settings.config
more strictly than the CLI. After the first build of a new project, open it in the GUI once too."""
import argparse, glob, json, math, os, re, shutil, statistics, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf

_NUM = re.compile(r'([XYZEFIJ])(-?[\d.]+)')

def layer_times(gcode, accel=10000.0):
    """{layer: [seconds, z]} estimated from moves: length / feedrate with a stop at each end. Close to Bambu's own numbers
    (within ~10 %), good enough to compare layers. Layers come from Bambu's 'M73 L<n>' markers."""
    t = {}; L = 0; x = y = zh = 0.0; f = 3000.0; rel = False
    for line in open(gcode, errors='ignore'):
        if line.startswith('M73 L'):
            L = int(line[5:].split()[0]); t.setdefault(L, [0.0, zh]); continue
        if line.startswith('; Z_HEIGHT:'):  # comes just before the layer's M73 L
            zh = float(line.split(':')[1]); continue
        c = line.split(';', 1)[0].split()
        if not c: continue
        g = c[0]
        if g == 'G91': rel = True
        elif g == 'G90': rel = False
        if g not in ('G0', 'G1', 'G2', 'G3'): continue
        p = {k: float(v) for k, v in _NUM.findall(' '.join(c[1:]))}
        f = p.get('F', f)
        nx = x + p.get('X', 0) if rel else p.get('X', x); ny = y + p.get('Y', 0) if rel else p.get('Y', y)
        d = math.hypot(nx - x, ny - y)
        if g in ('G2', 'G3') and ('I' in p or 'J' in p):
            cx, cy = x + p.get('I', 0), y + p.get('J', 0)
            a = math.atan2(ny - cy, nx - cx) - math.atan2(y - cy, x - cx)
            if g == 'G3' and a <= 0: a += 2 * math.pi
            if g == 'G2' and a >= 0: a -= 2 * math.pi
            d = abs(a) * math.hypot(x - cx, y - cy)
        if d and L:
            v = f / 60.0; ta = v / accel
            t[L][0] += d / v + ta if d > v * ta else 2 * math.sqrt(d / accel)
        x, y = nx, ny
    return t

def uneven_layers(times, ratio=1.5):
    """Runs of layers (first layer skipped) whose time is off the median by more than ratio: [(first, last, z0, z1, s_min, s_max)]."""
    ks = sorted(k for k in times if k > 1)
    if len(ks) < 5: return []
    med = statistics.median(times[k][0] for k in ks); runs = []
    for k in ks:
        s = times[k][0]
        if s > med * ratio or s < med / ratio:
            if runs and runs[-1][1] == k - 1:
                r = runs[-1]; runs[-1] = (r[0], k, r[2], times[k][1], min(r[4], s), max(r[5], s))
            else: runs.append((k, k, times[k][1], times[k][1], s, s))
    return med, runs

def plate_filament_types(text):
    """Filament types the plate prints with. '; filament_type =' lists every slot of the project; the header line
    '; filament: 3' (1-based, comma-separated) names the slots this plate uses. A PETG plate in a project that also holds
    PLA must not get the PLA checks."""
    m = re.search(r'^; filament_type = (.*)$', text, re.M)
    types = [x.strip() for x in m.group(1).split(';')] if m else []
    u = re.search(r'^; filament: ([\d, ]+)$', text, re.M)
    if not u: return types
    used = [int(x) for x in re.findall(r'\d+', u.group(1))]
    return [types[i - 1] for i in used if 0 < i <= len(types)]

def check_layer_times(gcode):
    types = plate_filament_types(open(gcode, errors='ignore').read())
    if not any(x.upper().startswith('PLA') for x in types): return
    res = uneven_layers(layer_times(gcode))
    if not res or not res[1]: return
    med, runs = res
    print('  LAYER TIME (PLA): typical layer %.0f s; these layers differ and can show as bands on the walls:' % med)
    for a, b, z0, z1, lo, hi in runs:
        print('    layers %d-%d (Z %.2f-%.2f mm): %.0f-%.0f s' % (a, b, z0, z1, lo, hi))
    print('  Fix: see "Even layer times on PLA" in the bambu-project skill.')

def pause_heights(gcode, pause_cmds=('M400 U1', 'M601')):
    """[(top_z, printed_top)] per pause. Bambu writes the pause right after the layer change to the layer whose top is top_z
    ('; Z_HEIGHT: top_z'), before printing it: at the pause the part stands printed_top = the previous layer's top high."""
    out, z, prev = [], 0.0, 0.0
    for line in open(gcode, errors='ignore'):
        if line.startswith('; Z_HEIGHT:'): prev, z = z, float(line.split(':')[1])
        elif line.strip() in pause_cmds: out.append((z, prev))
    return out

def conflict_lines(log):
    """The lines of the CLI output that name an object in a conflict (keep-out area, overlap, off the plate)."""
    keys = ('too close to', 'conflict', 'outside', 'overlap', 'clumping')
    return [l.strip() for l in log.splitlines() if any(k in l.lower() for k in keys) and 'error_string' not in l]

def main():
    a = argparse.ArgumentParser(); a.add_argument('project'); a.add_argument('--plate', type=int, default=0); a.add_argument('--keep')
    a = a.parse_args(); exe = bambu3mf.studio_path()
    if not exe: sys.exit('Bambu Studio not found: set BAMBU_STUDIO to its executable')
    out = a.keep or tempfile.mkdtemp(prefix='slice_check_'); os.makedirs(out, exist_ok=True)
    copy = os.path.join(out, 'in.3mf'); shutil.copy(a.project, copy)
    r = subprocess.run([exe, '--slice', str(a.plate), '--outputdir', out, copy], cwd=out, capture_output=True, text=True, timeout=1800)
    res = os.path.join(out, 'result.json'); data = json.load(open(res)) if os.path.exists(res) else {}
    if r.returncode or data.get('return_code', 0):
        print((r.stdout + r.stderr)[-1500:]); print('result:', {k: v for k, v in data.items() if k != 'sliced_plates'})
        named = conflict_lines(r.stdout + r.stderr)
        if named: print('Objects named in the failure (move them; add_plate.py --at):\n  ' + '\n  '.join(dict.fromkeys(named)))
        sys.exit('SLICE FAILED')
    total_s = total_g = 0
    for p in data.get('sliced_plates', []):
        s = p.get('sliced_time_with_cache') or p.get('sliced_time') or 0
        g = sum(f.get('total_used_g', 0) for f in p.get('filaments', [])) or 0
        print('plate %s: %s objects %s' % (p.get('id'), ('%.1f g' % g) if g else '', [o.get('name') for o in p.get('objects', [])])); total_g += g
    for gc in sorted(glob.glob(os.path.join(out, 'plate_*.gcode'))):
        print(os.path.basename(gc) + ':'); check_layer_times(gc)
        ph = pause_heights(gc)
        for z, top in ph: print('  pause at top_z %.2f: the part is printed up to %.2f mm; an insert must not stand above %.2f' % (z, top, z))
    print('OK', '%.1f g' % total_g if total_g else '', ('output in ' + out) if a.keep else '')
    if not a.keep: shutil.rmtree(out, ignore_errors=True)

if __name__ == '__main__': main()
