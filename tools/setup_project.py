#!/usr/bin/env python3
"""Set a folder up for the Fusion -> Bambu workflow. Safe to run again: what exists is left alone, CLAUDE.md gets its section once.
    python3 setup_project.py <dir> --name "Bracket" [--proj Bracket.3mf] [--template <name-or-path>] [--fusion-doc "Bracket"]
    python3 setup_project.py <dir> --update-tools        refresh only the vendored tools"""
import argparse, json, os, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__)); PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, 'bambu')); import bambu_template
GITIGNORE = ['.DS_Store', '__pycache__/', '.shots/', '.claude/settings.local.json', 'result.json', 'bambulab/.fusion_export_log.jsonl', 'bambulab/.backups/']

def say(what, path): print('%-9s %s' % (what, path))

def fill(text, v):
    for k, x in v.items(): text = text.replace('{{%s}}' % k, x)
    return text

def write_new(path, text):
    if os.path.exists(path): say('kept', path); return
    os.makedirs(os.path.dirname(path), exist_ok=True); open(path, 'w', encoding='utf-8').write(text); say('created', path)

def vendor(root):
    version = json.load(open(os.path.join(PLUGIN, '.claude-plugin', 'plugin.json')))['version']
    for src, dst in (('bambu', 'bambulab/tools'), ('fusion', 'fusion/tools')):
        d = os.path.join(root, dst); os.makedirs(d, exist_ok=True)
        for f in sorted(os.listdir(os.path.join(HERE, src))):
            if f.endswith(('.py', '.json')): shutil.copy(os.path.join(HERE, src, f), d)
        open(os.path.join(d, 'VERSION'), 'w').write('fusion-bambulab %s\n' % version); say('vendored', d)

def main():
    a = argparse.ArgumentParser(); a.add_argument('dir'); a.add_argument('--name'); a.add_argument('--proj'); a.add_argument('--template')
    a.add_argument('--fusion-doc'); a.add_argument('--update-tools', action='store_true'); a = a.parse_args()
    root = os.path.abspath(a.dir)
    if a.update_tools: vendor(root); return
    if not a.name: sys.exit('--name is required')
    proj = a.proj or re.sub(r'[^\w-]+', '', a.name.title()) + '.3mf'
    v = {'NAME': a.name, 'PROJ': proj, 'FUSION_DOC': a.fusion_doc or a.name}
    tpl = lambda f: fill(open(os.path.join(PLUGIN, 'templates', f), encoding='utf-8').read(), v)
    for d in ('stl', '.shots', 'bambulab'): os.makedirs(os.path.join(root, d), exist_ok=True)
    if os.path.exists(os.path.join(root, 'bambulab', 'tools', 'VERSION')): say('kept', 'vendored tools (--update-tools refreshes them)')
    else: vendor(root)
    write_new(os.path.join(root, 'fusion', 'parts.json'), '{}\n')
    write_new(os.path.join(root, 'DESIGN_RULES.md'), tpl('DESIGN_RULES.md'))
    write_new(os.path.join(root, 'bambulab', 'build_3mf.py'), tpl('build_3mf.py'))
    md = os.path.join(root, 'CLAUDE.md'); old = open(md, encoding='utf-8').read() if os.path.exists(md) else ''
    if 'fusion-bambulab:start' in old: say('kept', md)
    else:
        open(md, 'w', encoding='utf-8').write((old.rstrip() + '\n\n' if old.strip() else '') + tpl('CLAUDE.md')); say('updated', md)
    gi = os.path.join(root, '.gitignore'); have = open(gi, encoding='utf-8').read().splitlines() if os.path.exists(gi) else []
    add = [l for l in GITIGNORE if l not in have]
    if add: open(gi, 'w', encoding='utf-8').write('\n'.join(have + add) + '\n'); say('updated', gi)
    dst = os.path.join(root, 'bambulab', 'template.3mf')
    if os.path.exists(dst): say('kept', dst)
    elif a.template:
        src = bambu_template.path_of(a.template)
        if not os.path.exists(src): sys.exit('no template %s: run bambu_template.py make first' % src)
        shutil.copy(src, dst); say('created', dst); bambu_template.show(dst)
    else: print('NO TEMPLATE: bambulab/template.3mf is missing. Make one with bambu_template.py, then run this again with --template.')

if __name__ == '__main__': main()
