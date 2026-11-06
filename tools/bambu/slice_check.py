#!/usr/bin/env python3
"""Slice a project with the Bambu Studio CLI, on a copy, and print time and grams per plate. Exit 1 if slicing fails.
    python3 bambulab/tools/slice_check.py <project.3mf> [--plate N] [--keep <dir>]
Safe while the GUI is open. It proves the project slices, NOT that the GUI opens it: the GUI validates project_settings.config
more strictly than the CLI. After the first build of a new project, open it in the GUI once too."""
import argparse, json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf

def main():
    a = argparse.ArgumentParser(); a.add_argument('project'); a.add_argument('--plate', type=int, default=0); a.add_argument('--keep')
    a = a.parse_args(); exe = bambu3mf.studio_path()
    if not exe: sys.exit('Bambu Studio not found: set BAMBU_STUDIO to its executable')
    out = a.keep or tempfile.mkdtemp(prefix='slice_check_'); os.makedirs(out, exist_ok=True)
    copy = os.path.join(out, 'in.3mf'); shutil.copy(a.project, copy)
    r = subprocess.run([exe, '--slice', str(a.plate), '--outputdir', out, copy], cwd=out, capture_output=True, text=True, timeout=1800)
    res = os.path.join(out, 'result.json'); data = json.load(open(res)) if os.path.exists(res) else {}
    if r.returncode or data.get('return_code', 0):
        print((r.stdout + r.stderr)[-1500:]); print('result:', {k: v for k, v in data.items() if k != 'sliced_plates'}); sys.exit('SLICE FAILED')
    total_s = total_g = 0
    for p in data.get('sliced_plates', []):
        s = p.get('sliced_time_with_cache') or p.get('sliced_time') or 0
        g = sum(f.get('total_used_g', 0) for f in p.get('filaments', [])) or 0
        print('plate %s: %s objects %s' % (p.get('id'), ('%.1f g' % g) if g else '', [o.get('name') for o in p.get('objects', [])])); total_g += g
    print('OK', '%.1f g' % total_g if total_g else '', ('output in ' + out) if a.keep else '')
    if not a.keep: shutil.rmtree(out, ignore_errors=True)

if __name__ == '__main__': main()
