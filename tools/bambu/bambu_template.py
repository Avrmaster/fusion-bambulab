#!/usr/bin/env python3
"""Printer templates: the printer, nozzle, process and filament presets of a Bambu project, without its parts.
A template lives in ~/.config/fusion-bambulab/templates/<name>.3mf (FUSION_BAMBULAB_HOME moves it) and is reused by every new project.
    python3 bambu_template.py make <saved-project.3mf> <name>     make a template from any project saved by Bambu Studio
    python3 bambu_template.py list                                the templates on this machine
    python3 bambu_template.py show <name-or-path.3mf>             printer, process, filaments, overrides"""
import json, os, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf
HOME = os.environ.get('FUSION_BAMBULAB_HOME') or os.path.expanduser('~/.config/fusion-bambulab')
KEEP = ('[Content_Types].xml', '_rels/.rels', 'Metadata/slice_info.config', 'Metadata/project_settings.config')

def path_of(name): return name if os.path.exists(name) else os.path.join(HOME, 'templates', name + '.3mf')

def show(p):
    _, files = bambu3mf.load(p); ps = json.loads(files['Metadata/project_settings.config'])
    print('printer  ', ps.get('printer_settings_id')); print('process  ', ps.get('print_settings_id')); print('bed      ', bambu3mf.bed_size(ps), 'mm')
    print('nozzle   ', ps.get('nozzle_diameter')); print('filaments', list(dict.fromkeys(ps['filament_settings_id'])))
    over = (ps.get('different_settings_to_system') or [''])[0]
    print('process overrides inherited by every new project:', over or 'none')

def make(src, name):
    infos, files = bambu3mf.load(src); miss = [k for k in KEEP[:2] + KEEP[3:] if k not in files]
    if miss: sys.exit('%s is not a saved Bambu project: no %s' % (src, miss))
    dst = path_of(name) if not name.endswith('.3mf') else name
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    bambu3mf.save(dst, files, [k for k in KEEP if k in files]); print('wrote', dst); show(dst)

def main():
    a = sys.argv[1:]
    if a[:1] == ['make'] and len(a) == 3: make(a[1], a[2])
    elif a[:1] == ['show'] and len(a) == 2: show(path_of(a[1]))
    elif a[:1] == ['list']:
        d = os.path.join(HOME, 'templates')
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []: print(f[:-4])
    else: sys.exit(__doc__)

if __name__ == '__main__': main()
