#!/usr/bin/env python3
"""Set a folder up for the Fusion -> Bambu workflow. Safe to run again: what exists is left alone, CLAUDE.md gets its section once.
    python3 setup_project.py <dir> --name "Bracket" [--proj Bracket.3mf] [--template <name-or-path>] [--fusion-doc "Bracket"]
    python3 setup_project.py <dir> --cnc "<machine>" [--lang uk]   add the CNC operator station in <dir>/cnc (also alone)
    python3 setup_project.py <dir> --gate [--name "Bracket"] [--proj Bracket.3mf] [--fusion-doc "Bracket"]
                                                         add the print gate in <dir>/gate (also alone, in a project that exists)
    python3 setup_project.py <dir> --update-tools        refresh only the vendored tools (cnc/ too when it exists, the gate too)
--gate copies tools/gate -> gate/tools and rules -> gate/rules with gate/LOCK.json, the ledger tools and meshid.py ->
bambulab/tools, creates gate/project.json (mode observe), gate/layout.json, gate/owner/params.json and gate/RULES.md, turns
the print ledger on and adds the .gitignore lines. It PRINTS the permission rules for .claude/settings.json and the lines
for CLAUDE.md and writes neither: those two belong to the owner. A vendored gate that was changed by hand (forked, or a
copy without gate/LOCK.json that differs from the plugin) is listed and left alone unless --overwrite-fork is given; when
that leaves the folder without gate/tools/gate.py, --gate writes nothing at all (no project file, no ledger)."""
import argparse, importlib, json, os, re, shutil, sys, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, 'bambu')); import bambu_template
CNC_GITIGNORE = ['cnc/laptop/laptop.json', 'cnc/station/jobs.js']
GATE_GITIGNORE = ['gate/run/', 'gate/reports/guard.log', 'bambulab/ledger/local.json', 'bambulab/ledger/local/']
GATE_START, GATE_END = '<!-- fusion-bambulab:gate:start -->', '<!-- fusion-bambulab:gate:end -->'
GITIGNORE = ['.DS_Store', '__pycache__/', '.shots/', '.claude/settings.local.json', 'result.json', 'bambulab/.fusion_export_log.jsonl', 'bambulab/.backups/']

def say(what, path): print('%-9s %s' % (what, path))

def fill(text, v):
    for k, x in v.items(): text = text.replace('{{%s}}' % k, x)
    return text

def write_new(path, text):
    if os.path.exists(path): say('kept', path); return
    os.makedirs(os.path.dirname(path), exist_ok=True); open(path, 'w', encoding='utf-8').write(text); say('created', path)

def vendor(root, overwrite_fork=False):
    version = json.load(open(os.path.join(PLUGIN, '.claude-plugin', 'plugin.json')))['version']
    gated = os.path.isfile(os.path.join(root, 'gate', 'project.json')) or os.path.isfile(os.path.join(root, 'gate', 'LOCK.json'))
    st = gate_module('gate_vendor').lock_state(root) if gated else None     # read BEFORE the copy below: the lock also covers meshid.py and the ledger tools
    keep = gate_module('gate_vendor').SIBLING_FILES if st and st['state'] == 'forked' and not overwrite_fork else ()   # a forked gate is left alone, these four files too
    for src, dst in (('bambu', 'bambulab/tools'), ('fusion', 'fusion/tools')):
        d = os.path.join(root, dst); os.makedirs(d, exist_ok=True)
        for f in sorted(os.listdir(os.path.join(HERE, src))):
            if f.endswith(('.py', '.json')) and not (src == 'bambu' and f in keep): shutil.copy(os.path.join(HERE, src, f), d)
        if src == 'bambu':                                    # the print ledger lives beside bambu3mf.py, which calls it
            for f in sorted(os.listdir(os.path.join(HERE, 'ledger'))):
                if f.endswith('.py') and f not in keep: shutil.copy(os.path.join(HERE, 'ledger', f), d)
        open(os.path.join(d, 'VERSION'), 'w').write('fusion-bambulab %s\n' % version); say('vendored', d)
    if os.path.isdir(os.path.join(root, 'cnc')): vendor_cnc(root, version)
    if gated and vendor_gate(root, overwrite_fork, st):
        propose(root, None)                               # the permission rules of this version, and those of an older one to remove

def gate_module(name):
    """A module of tools/gate, tools/ledger or tools/bambu of THIS plugin (never the copy inside a project)."""
    for d in ('bambu', 'ledger', 'gate'):
        if os.path.join(HERE, d) not in sys.path: sys.path.insert(0, os.path.join(HERE, d))
    return importlib.import_module(name)

def vendor_gate(root, overwrite_fork=False, st=None):
    """The ledger tools and meshid.py -> bambulab/tools, tools/gate -> gate/tools, rules -> gate/rules, then gate/LOCK.json.
    False when the copy in the project was changed by hand and is left as it is. `st` is the lock state as it was before
    the caller copied anything into bambulab/tools (vendor() replaces files the lock covers); None = read it now."""
    gate_vendor = gate_module('gate_vendor'); st = st or gate_vendor.lock_state(root)
    if st['state'] == 'forked' and not overwrite_fork:
        say('FORKED', os.path.join(root, 'gate') + ': ' + ', '.join(st['changed'] + st['missing'] + st['extra']))
        print('          not refreshed. Move the change into the plugin, or run again with --overwrite-fork to drop it.')
        print('          meshid.py and the ledger tools in bambulab/tools were kept as they are too: they stay at the version of gate/LOCK.json.'); return False
    if st['state'] == 'absent' and not overwrite_fork:       # no lock: nothing says the files under gate/tools and gate/rules came from the plugin
        have, ours = gate_vendor._vendored_hashes(root), {}
        for name, parts in gate_vendor.VENDORED: ours.update(gate_vendor.tree_hashes(os.path.join(PLUGIN, *parts), name + '/'))
        own = sorted(p for p in have if ours.get(p) != have[p])
        if own:
            say('NO LOCK', os.path.join(root, 'gate') + ': ' + ', '.join(own))
            print('          gate/LOCK.json is missing and these files are not the plugin\'s: vendoring would overwrite or delete them. Move them out, or run again with --overwrite-fork.'); return False
    d = os.path.join(root, 'bambulab', 'tools'); os.makedirs(d, exist_ok=True)
    for f in sorted(os.listdir(os.path.join(HERE, 'ledger'))):
        if f.endswith('.py'): shutil.copy(os.path.join(HERE, 'ledger', f), d)
    shutil.copy(os.path.join(HERE, 'bambu', 'meshid.py'), d)
    lock = gate_vendor.vendor(PLUGIN, root); say('vendored', '%s (%d files, LOCK.json %s)' % (os.path.join(root, 'gate'), len(lock['files']), lock['version']))
    if os.path.isfile(os.path.join(root, 'gate', 'project.json')):          # gate/RULES.md is generated: it follows the rules just vendored
        try: say('wrote', gate_module('gate_render').write_project_rules(root, gate_module('gate_registry').load(), None))
        except Exception as e: say('NOT DONE', 'gate/RULES.md: %s' % e)
    return True

def fill_json(obj, v):
    """A template value with its {{KEY}} placeholders filled; a string that is one placeholder takes the value's own type."""
    if isinstance(obj, dict): return {fill_json(k, v): fill_json(x, v) for k, x in obj.items()}
    if isinstance(obj, list): return [fill_json(x, v) for x in obj]
    if not isinstance(obj, str): return obj
    m = re.fullmatch(r'\{\{(\w+)\}\}', obj)
    return v[m.group(1)] if m and m.group(1) in v else fill(obj, {k: str(x) for k, x in v.items()})

FIT_SETTINGS = ('layer_height', 'initial_layer_print_height', 'xy_hole_compensation', 'xy_contour_compensation', 'elefant_foot_compensation', 'wall_generator')
PRINT_SETTINGS = ('enable_support', 'support_type', 'support_remove_small_overhang', 'wall_sequence')     # a target's "print" block

def read_target(path):
    """{"nozzle", "line_width", "material"} from the project settings of a .3mf, or None when they cannot be read. The
    slicer settings every fit is measured under (FIT_SETTINGS) are added when the file holds them: a key it does not
    hold is left out, never invented."""
    try:
        with zipfile.ZipFile(path) as z: ps = json.loads(z.read('Metadata/project_settings.config'))
        first = lambda x: x[0] if isinstance(x, list) else x
        nozzle, width, material = float(first(ps['nozzle_diameter'])), float(first(ps['line_width'])), str(first(ps['filament_type']))
        if not (nozzle > 0 and width > 0 and material): return None
        target = {'nozzle': nozzle, 'line_width': width, 'material': material}
        for k in FIT_SETTINGS:
            v = first(ps.get(k))
            if v is None or v == '': continue
            try: target[k] = float(v)
            except (TypeError, ValueError): target[k] = str(v)
        printing = {}                       # the print settings the gate compares too (supports, wall order): as the file holds them
        for k in PRINT_SETTINGS:
            v = first(ps.get(k))
            if v is None or v == '': continue
            try: printing[k] = float(v)
            except (TypeError, ValueError): printing[k] = str(v)
        if printing: target['print'] = printing
        return target
    except Exception: return None

def claude_sections():
    """(the section every project gets, the lines about the gate) of templates/CLAUDE.md."""
    text = open(os.path.join(PLUGIN, 'templates', 'CLAUDE.md'), encoding='utf-8').read(); i = text.find(GATE_START)
    return (text, '') if i < 0 else (text[:i].rstrip('\n') + '\n', text[i:].rstrip('\n') + '\n')

def setup_gate(root, proj=None, doc=None, overwrite_fork=False):
    md = os.path.join(root, 'CLAUDE.md'); old = open(md, encoding='utf-8').read() if os.path.exists(md) else ''
    if not doc:
        m = re.search(r'^- Fusion document: (.+)$', old, re.M); doc = m.group(1).strip() if m else None
    if not proj:
        m = re.search(r'^- Bambu project: `bambulab/([^`]+)`', old, re.M); proj = m.group(1) if m else None
        found = sorted(f for f in os.listdir(os.path.join(root, 'bambulab')) if f.endswith('.3mf') and f != 'template.3mf') if os.path.isdir(os.path.join(root, 'bambulab')) else []
        if not proj and len(found) == 1: proj = found[0]
    gate_common = gate_module('gate_common')
    if os.path.exists(os.path.join(root, 'gate', 'LOCK.json')) and gate_module('gate_vendor').lock_state(root)['state'] != 'forked':
        say('kept', 'vendored gate (--update-tools refreshes it)')
    elif not vendor_gate(root, overwrite_fork) and not os.path.isfile(os.path.join(root, 'gate', 'tools', 'gate.py')):
        # nothing was copied and no gate tool is there: project files without the tools would be a gated project that cannot run
        print('NOT SET UP the gate tools were not copied, so nothing else was written either (no project file, no ledger). Deal with the files listed above, or run again with --overwrite-fork.'); return
    target = next((t for t in (read_target(os.path.join(root, 'bambulab', f)) for f in ([proj] if proj else []) + ['template.3mf']) if t), None)
    v = {'FUSION_DOC': doc or '', 'DOC_KEY': gate_common.slug(doc or 'main'), 'PROJ': proj or '', 'PROJ_KEY': gate_common.slug(os.path.splitext(proj or 'main')[0])}
    v.update({k.upper(): x for k, x in (target or {'nozzle': 0, 'line_width': 0, 'material': ''}).items()})
    load = lambda f: fill_json(json.load(open(os.path.join(PLUGIN, 'templates', 'gate', f), encoding='utf-8')), v)
    project, layout = load('project.json'), load('layout.json')
    if not doc: layout['documents'] = []
    if not proj: layout['projects'] = []
    if not proj or not target: project['targets'] = {}
    else: project['targets'] = {key: {k: x for k, x in t.items() if not (isinstance(x, str) and x.startswith('{{'))} for key, t in project['targets'].items()}   # a setting the file does not hold is not declared
    dump = lambda x: json.dumps(x, indent=1, ensure_ascii=False) + '\n'
    for path, text in (('gate/project.json', dump(project)), ('gate/layout.json', dump(layout)), ('gate/owner/params.json', dump(load('params.json')))):
        write_new(os.path.join(root, *path.split('/')), text)
    gitignore(root, GATE_GITIGNORE)
    for line in gate_module('ledger').init(root): say('ledger', line)
    reg = gate_module('gate_registry').load()
    try: say('wrote', gate_module('gate_render').write_project_rules(root, reg, None))
    except gate_common.GateError as e: say('NOT DONE', 'gate/RULES.md: %s' % e)
    if not doc: print('NO DOCUMENT: gate/layout.json registers no Fusion document. Add {"key", "name", "stl_dir"} under "documents" (or run again with --fusion-doc).')
    if not proj: print('NO PROJECT: gate/layout.json registers no .3mf project. Add {"key", "file", "stl_dir"} under "projects" (or run again with --proj).')
    elif not target: print('NO TARGET: the nozzle, line width and material of %s could not be read. Declare them in gate/project.json "targets".' % proj)
    else:
        print('TARGET    %s: nozzle %s, line width %s, %s (read from the .3mf; the owner confirms it in gate/project.json)' % (v['PROJ_KEY'], target['nozzle'], target['line_width'], target['material']))
        have = [k for k in FIT_SETTINGS if k in target]
        if have: print('          fit settings as the file holds them today: %s. The owner confirms each one: every fit counts as measured under these values, and a tool write may have changed one before the gate was here.' % ', '.join('%s %s' % (k, target[k]) for k in have))
        if len(have) < len(FIT_SETTINGS): print('          not in the file, to declare by hand in gate/project.json "targets": %s' % ', '.join(k for k in FIT_SETTINGS if k not in target))
        miss = [k for k in PRINT_SETTINGS if k not in (target.get('print') or {})]
        if miss: print('          print settings not in the file, to declare by hand under "print" of that target: %s' % ', '.join(miss))
    if os.path.exists(os.path.join(root, 'bambulab', 'tools', 'bambu3mf.py')) and 'gate_guard' not in open(os.path.join(root, 'bambulab', 'tools', 'bambu3mf.py'), encoding='utf-8').read():
        print('OLD TOOLS bambulab/tools/bambu3mf.py has no guard yet: --update-tools brings the .3mf writer that asks the gate.')
    propose(root, old)

def propose(root, claude_md):
    """Print what belongs to the owner: the permission rules (and the rules of an older template to remove) and the
    CLAUDE.md lines (claude_md None: the text of the project's CLAUDE.md is read here). Nothing is written."""
    if claude_md is None:
        md = os.path.join(root, 'CLAUDE.md'); claude_md = open(md, encoding='utf-8').read() if os.path.exists(md) else ''
    want = json.load(open(os.path.join(PLUGIN, 'templates', 'gate', 'settings.json'), encoding='utf-8'))
    path = os.path.join(root, '.claude', 'settings.json')
    try: perms = json.load(open(path, encoding='utf-8')).get('permissions', {}) if os.path.exists(path) else {}
    except Exception: perms = {}
    missing = {kind: [r for r in want['permissions'].get(kind, []) if r not in (perms.get(kind) or [])] for kind in ('deny', 'ask')}
    missing = {k: v for k, v in missing.items() if v}
    if missing:
        print('\nPROPOSED  %s  (not written: the owner adds it). Agents may not edit the owner files at all (deny), and an owner command asks first (ask):' % path)
        print(json.dumps({'permissions': missing}, indent=2))
    else: say('kept', path + ' (the rules for the owner files are there)')
    old_rules = gate_module('gate_common').obsolete_rules(root)
    if old_rules:
        print('\nPROPOSED REMOVAL  (not written: the owner removes them) rules of an older template that put a prompt in front of ordinary gate commands, '
              'or ask where the template now denies:\n  ' + '\n  '.join(old_rules))
    lines = claude_sections()[1]
    if GATE_START in claude_md:
        say('kept', os.path.join(root, 'CLAUDE.md') + ' (the gate lines are there)')
        # the owner words his own section; a command of this version's lines that it never names is said, not written
        new = [l for l in lines.splitlines() if l.startswith('- ') and any(c not in claude_md for c in re.findall(r'`([^`]*gate\.py [^`]*)`', l))]
        if new:
            print('NOTE      %s names no command of these lines of this version (templates/CLAUDE.md); the owner decides whether they go in:\n%s'
                  % (os.path.join(root, 'CLAUDE.md'), '\n'.join(new)))
    elif lines: print('\nPROPOSED  %s  (not written: the owner adds these lines)\n%s' % (os.path.join(root, 'CLAUDE.md'), lines))
    print('NEXT      python3 gate/tools/gate.py doctor   then   python3 gate/tools/gate.py run --audit   (lists what the project already has; nothing blocks)')

def vendor_cnc(root, version):
    src, d = os.path.join(HERE, 'cnc'), os.path.join(root, 'cnc')
    for sub in ('', 'station', 'laptop'):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
        for f in sorted(os.listdir(os.path.join(src, sub))):
            if f.endswith(('.py', '.sh', '.ps1', '.html')): shutil.copy(os.path.join(src, sub, f), os.path.join(d, sub))
    open(os.path.join(d, 'VERSION'), 'w').write('fusion-bambulab %s\n' % version); say('vendored', d)

def setup_cnc(root, machine, lang):
    v = {'MACHINE': machine, 'LANG': lang}
    tpl = lambda f: fill(open(os.path.join(PLUGIN, 'templates', 'cnc', f), encoding='utf-8').read(), v)
    os.makedirs(os.path.join(root, 'cnc'), exist_ok=True)
    vendor_cnc(root, json.load(open(os.path.join(PLUGIN, '.claude-plugin', 'plugin.json')))['version'])
    write_new(os.path.join(root, 'cnc', 'station.json'), tpl('station.json'))
    if not os.path.isdir(os.path.join(root, 'cnc', 'jobs')):
        write_new(os.path.join(root, 'cnc', 'jobs', '00-connect', 'job.json'), tpl(os.path.join('jobs', '00-connect', 'job.json')))
    gitignore(root, CNC_GITIGNORE)

def gitignore(root, lines):
    gi = os.path.join(root, '.gitignore'); have = open(gi, encoding='utf-8').read().splitlines() if os.path.exists(gi) else []
    add = [l for l in lines if l not in have]
    if add: open(gi, 'w', encoding='utf-8').write('\n'.join(have + add) + '\n'); say('updated', gi)

def main():
    a = argparse.ArgumentParser(); a.add_argument('dir'); a.add_argument('--name'); a.add_argument('--proj'); a.add_argument('--template')
    a.add_argument('--fusion-doc'); a.add_argument('--update-tools', action='store_true')
    a.add_argument('--cnc', metavar='MACHINE'); a.add_argument('--lang', default='en')
    a.add_argument('--gate', action='store_true'); a.add_argument('--overwrite-fork', action='store_true'); a = a.parse_args()
    root = os.path.abspath(a.dir)
    if a.update_tools: vendor(root, a.overwrite_fork); return
    if a.cnc: setup_cnc(root, a.cnc, a.lang)
    if not a.name:
        if a.gate: setup_gate(root, a.proj, a.fusion_doc, a.overwrite_fork)
        if a.gate or a.cnc: return
        sys.exit('--name is required')
    proj = a.proj or re.sub(r'[^\w-]+', '', a.name.title()) + '.3mf'
    v = {'NAME': a.name, 'PROJ': proj, 'FUSION_DOC': a.fusion_doc or a.name}
    tpl = lambda f: fill(claude_sections()[0] if f == 'CLAUDE.md' else open(os.path.join(PLUGIN, 'templates', f), encoding='utf-8').read(), v)
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
    gitignore(root, GITIGNORE)
    dst = os.path.join(root, 'bambulab', 'template.3mf')
    if os.path.exists(dst): say('kept', dst)
    elif a.template:
        src = bambu_template.path_of(a.template)
        if not os.path.exists(src): sys.exit('no template %s: run bambu_template.py make first' % src)
        shutil.copy(src, dst); say('created', dst); bambu_template.show(dst)
    else: print('NO TEMPLATE: bambulab/template.3mf is missing. Make one with bambu_template.py, then run this again with --template.')
    if a.gate: setup_gate(root, proj, v['FUSION_DOC'], a.overwrite_fork)

if __name__ == '__main__': main()
