"""The vendored copy of the gate in a project and its lock: is it intact, forked or stale.

`setup_project.py --gate` copies tools/gate -> gate/tools, rules -> gate/rules and writes gate/LOCK.json:

    {"plugin": "fusion-bambulab", "version": "0.2.0", "time": "...",
     "files": {"tools/gate.py": "<sha256>", "tools/probes/_geom.py": "...", "rules/registry/inserts.json": "...", ...},
     "siblings": {"bambulab/tools/meshid.py": "<sha256>", "bambulab/tools/ledger.py": "...", ...}}

"files" are paths under gate/. "siblings" are the tools the gate loads from bambulab/tools (meshid and the ledger),
with paths from the project root; the key is absent in a lock written before they were vendored.

States:
  intact  every file of the lock has its hash and no other .py or .json lies in gate/tools or gate/rules
  forked  a locked file was changed, is missing, or an extra file was added (the list says which)
  stale   intact, and the installed plugin has a newer version than the lock (a warning, not a block)
  absent  no gate/LOCK.json (the plugin's own repository, or a copy made by hand)

A forked copy blocks: its judges are no longer the ones the plugin tested. The way out is to move the change into the
plugin and vendor again, or to vendor again and lose the change."""
import os, shutil, sys

import gate_common
from gate_common import GateError, EXIT_INFRA, EXIT_PASS, gate_path, read_json, sha256_file, write_json

PLUGIN_NAME = 'fusion-bambulab'
VENDORED = (('tools', ('tools', 'gate')), ('rules', ('rules',)))          # folder under gate/ <- path in the plugin
SIBLING_FILES = ('meshid.py', 'ledger.py', 'ledger_mesh.py', 'ledger_cache.py')   # bambulab/tools/: loaded through gate_common.sibling
SIBLING_DIR = ('bambulab', 'tools')
_SUFFIXES = ('.py', '.json')


def plugin_version(plugin_root):
    """"version" of <plugin_root>/.claude-plugin/plugin.json, or None when the file is not there."""
    path = os.path.join(plugin_root, '.claude-plugin', 'plugin.json')
    if not os.path.isfile(path):
        return None
    data = read_json(path)
    return data.get('version') if isinstance(data, dict) else None


def own_plugin_root():
    """The plugin this copy of the tools lies in (tools/gate two levels under a folder with .claude-plugin/plugin.json),
    or None for a vendored copy."""
    cand = os.path.normpath(os.path.join(gate_common.tools_dir(), '..', '..'))
    return cand if os.path.isfile(os.path.join(cand, '.claude-plugin', 'plugin.json')) else None


def tree_hashes(folder, prefix=''):
    """{relative path with forward slashes: sha256} of every .py and .json under a folder (no __pycache__), each path
    starting with `prefix`."""
    out = {}
    if not os.path.isdir(folder):
        return out
    for dirpath, dirnames, names in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
        for n in sorted(names):
            if n.endswith(_SUFFIXES):
                full = os.path.join(dirpath, n)
                out[prefix + os.path.relpath(full, folder).replace(os.sep, '/')] = sha256_file(full)
    return out


def _vendored_hashes(root):
    out = {}
    for name, _ in VENDORED:
        out.update(tree_hashes(gate_path(root, name), name + '/'))
    return out


def _sibling_hashes(root):
    out = {}
    for n in SIBLING_FILES:
        full = os.path.join(root, *(SIBLING_DIR + (n,)))
        if os.path.isfile(full):
            out['/'.join(SIBLING_DIR + (n,))] = sha256_file(full)
    return out


def _copy_tree(src, dst):
    """Copy every .py and .json of src into dst (same sub-folders) and remove the .py and .json of dst that src no
    longer has. Returns (copied, removed) as lists of relative paths."""
    want = tree_hashes(src)
    have = tree_hashes(dst)
    copied, removed = [], []
    for rel in sorted(want):
        target = os.path.join(dst, *rel.split('/'))
        if have.get(rel) != want[rel]:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(src, *rel.split('/')), target)
            copied.append(rel)
    for rel in sorted(set(have) - set(want)):
        os.remove(os.path.join(dst, *rel.split('/')))
        removed.append(rel)
    return copied, removed


def vendor(plugin_root, root):
    """Copy the gate into a project: <plugin>/tools/gate -> <root>/gate/tools (with probes/), <plugin>/rules ->
    <root>/gate/rules, files that left the plugin are removed from the copy, then write_lock(). Returns the lock dict.
    Nothing under gate/owner, gate/facts, gate/stamps or any other project data is touched."""
    version = plugin_version(plugin_root)
    if version is None:
        raise GateError('%s is not the plugin: it has no .claude-plugin/plugin.json' % plugin_root)
    for name, parts in VENDORED:
        src = os.path.join(plugin_root, *parts)
        if not os.path.isdir(src):
            raise GateError('the plugin has no %s folder' % '/'.join(parts))
        _copy_tree(src, gate_path(root, name))
    return write_lock(root, version)


def write_lock(root, version):
    """Write gate/LOCK.json from the files that lie in gate/tools and gate/rules now. Returns the lock dict."""
    lock = {'plugin': PLUGIN_NAME, 'version': version, 'time': gate_common.now(), 'files': _vendored_hashes(root)}
    siblings = _sibling_hashes(root)
    if siblings:
        lock['siblings'] = siblings
    write_json(gate_path(root, 'LOCK.json'), lock)
    return lock


def _version_key(text):
    out = []
    for piece in str(text or '').split('.'):
        digits = ''.join(c for c in piece if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def lock_state(root, plugin_root=None):
    """{"state": "intact" | "forked" | "stale" | "absent", "version": the lock's version or None,
    "plugin_version": the installed plugin's version or None, "changed": [paths], "missing": [paths], "extra": [paths]}.
    plugin_root None = no comparison with an installed plugin (the state is never "stale" then).

    Paths of gate/tools and gate/rules are relative to gate/ ("tools/gate.py"); paths of the sibling tools are relative
    to the project root ("bambulab/tools/meshid.py")."""
    out = {'state': 'absent', 'version': None, 'plugin_version': None, 'changed': [], 'missing': [], 'extra': []}
    if plugin_root:
        try:
            out['plugin_version'] = plugin_version(plugin_root)
        except GateError:
            out['plugin_version'] = None
    path = gate_path(root, 'LOCK.json')
    if not os.path.isfile(path):
        return out
    lock = read_json(path)
    if not isinstance(lock, dict) or not isinstance(lock.get('files'), dict):
        raise GateError('gate/LOCK.json has no "files": vendor the gate again (setup_project.py <dir> --gate)')
    out['version'] = lock.get('version')
    locked, now = lock['files'], _vendored_hashes(root)
    siblings = lock.get('siblings') if isinstance(lock.get('siblings'), dict) else {}
    now_siblings = _sibling_hashes(root)
    out['changed'] = sorted([p for p in locked if p in now and now[p] != locked[p]]
                            + [p for p in siblings if p in now_siblings and now_siblings[p] != siblings[p]])
    out['missing'] = sorted([p for p in locked if p not in now] + [p for p in siblings if p not in now_siblings])
    out['extra'] = sorted(p for p in now if p not in locked)
    if out['changed'] or out['missing'] or out['extra']:
        out['state'] = 'forked'
    elif out['plugin_version'] and _version_key(out['plugin_version']) > _version_key(out['version']):
        out['state'] = 'stale'
    else:
        out['state'] = 'intact'
    return out


def state_line(st):
    """One line about a lock state, for doctor and the session digest. '' for an intact copy."""
    if st['state'] == 'forked':
        names = st['changed'] + st['missing'] + st['extra']
        shown = ', '.join(names[:4]) + (' (+%d more)' % (len(names) - 4) if len(names) > 4 else '')
        return ('vendored gate is FORKED: %d changed, %d missing, %d extra (%s). Move the change into the plugin, then '
                'vendor again' % (len(st['changed']), len(st['missing']), len(st['extra']), shown))
    if st['state'] == 'stale':
        return ('vendored gate is version %s, the installed plugin is %s: `setup_project.py <dir> --update-tools` '
                'brings the new rules and checks' % (st['version'], st['plugin_version']))
    if st['state'] == 'absent':
        return 'no gate/LOCK.json: the gate is not vendored here (`setup_project.py <dir> --gate`)'
    return ''


# ---- doctor ---------------------------------------------------------------------------------------------------------------

def _line(out, level, what, text):
    out.append((level, '%-5s %-12s %s' % (level, what, text)))


def doctor_lines(root, pinger=None):
    """[(level, line)] of every check of `gate.py doctor`; level is "ok", "warn" or "error". pinger(url, timeout_s) ->
    bool replaces the real ping (tests); False as pinger skips the Fusion checks."""
    out = []
    v = sys.version_info
    _line(out, 'ok' if v >= (3, 9) else 'error', 'interpreter', 'Python %d.%d.%d%s' % (v[0], v[1], v[2], '' if v >= (3, 9) else ': the gate needs 3.9 or newer'))
    if root is None:
        _line(out, 'warn', 'project', 'not gated: no gate/project.json at or above %s' % os.getcwd())
        return out
    try:
        st = lock_state(root, own_plugin_root() or os.environ.get('CLAUDE_PLUGIN_ROOT') or None)
        level = {'intact': 'ok', 'stale': 'warn', 'absent': 'warn', 'forked': 'error'}[st['state']]
        _line(out, level, 'lock', state_line(st) or 'vendored gate is intact, version %s' % st['version'])
        for kind in ('changed', 'missing', 'extra'):
            for p in st[kind]:
                _line(out, 'error', '', '%s: %s' % (kind, p))
    except GateError as e:
        _line(out, 'error', 'lock', str(e))
    reg = resolved = project = layout = None
    try:
        import gate_registry
        reg = gate_registry.load_default()
        _line(out, 'ok', 'registry', '%d rules, %d parameters (%s)' % (len(reg['rules']), len(reg['params']), reg['dir']))
    except GateError as e:
        _line(out, 'error', 'registry', str(e))
    try:
        project = gate_common.load_project(root)
        targets = project.get('targets') or {}
        _line(out, 'ok', 'project.json', 'mode %s, %d declared target(s)' % (project['mode'], len(targets)))
        if not targets:
            _line(out, 'warn', 'project.json', 'no target declared: rules that depend on the nozzle use the registry default until "targets" names one')
        for key in sorted(targets):                  # what the fits are measured under: not declared = nothing compares the project file with it
            t = targets[key] if isinstance(targets[key], dict) else {}
            unsaid = [k for k in ('layer_height', 'initial_layer_print_height', 'xy_hole_compensation', 'xy_contour_compensation', 'elefant_foot_compensation', 'wall_generator') if t.get(k) is None]
            if unsaid:
                _line(out, 'warn', 'project.json', 'target %s does not say %s: project.settings_match cannot compare the project file with it, and every fit '
                                                   'counts as never printed for this project' % (key, ', '.join(unsaid)))
    except GateError as e:
        _line(out, 'error', 'project.json', str(e))
    try:
        layout = gate_common.load_layout(root)
        _line(out, 'ok', 'layout.json', '%d document(s), %d .3mf project(s), %d generator(s)'
              % (len(layout['documents']), len(layout['projects']), len(layout['generators'])))
    except GateError as e:
        _line(out, 'error', 'layout.json', str(e))
    if reg is not None and project is not None:
        import gate_params
        try:
            overrides = gate_params.load_overrides(root)
            for key in [None] + sorted(project.get('targets') or {}):
                resolved = gate_params.resolve(reg, overrides, gate_common.target_of(project, key))
            _line(out, 'ok', 'parameters', '%d set by the project' % len(overrides))
        except GateError as e:
            for text in getattr(e, 'errors', [str(e)]):
                _line(out, 'error', 'parameters', text)
    if layout is not None:
        if not layout['documents']:
            _line(out, 'warn', 'documents', 'gate/layout.json registers no Fusion document: nothing can be scanned')
        try:
            seen = gate_common.load_census(root)['docs']
        except GateError:
            seen = {}
        for d in layout['documents']:
            if not d.get('name'):
                _line(out, 'error', 'documents', 'document %r has no "name"' % (d.get('key'),))
            else:
                last = seen.get(d.get('key'))
                state = ('never scanned' if not last else
                         'open at the last scan (%s)' % last.get('scanned') if last.get('open') else
                         'not open when the gate last looked for it%s' % (' (last read %s)' % last['scanned'] if last.get('scanned') else ': never read'))
                _line(out, 'ok', 'documents', '%s = "%s": %s' % (d.get('key'), d['name'], state))
            stl_dir = d.get('stl_dir')
            if stl_dir and not os.path.isdir(os.path.join(root, *stl_dir.split('/'))):
                _line(out, 'warn', 'documents', 'STL folder %s of document %s does not exist yet' % (stl_dir, d.get('key')))
        for p in layout['projects']:
            f = p.get('file')
            if not f:
                _line(out, 'error', 'projects', 'project %r has no "file"' % (p.get('key'),))
            elif not os.path.isfile(os.path.join(root, *f.split('/'))):
                _line(out, 'warn', 'projects', '%s is registered and does not exist yet' % f)
            if project is not None and p.get('key') not in (project.get('targets') or {}):
                _line(out, 'warn', 'projects', 'no declared target for the project %r in gate/project.json' % (p.get('key'),))
            elif project is not None and f and os.path.isfile(os.path.join(root, *f.split('/'))):
                for text in _settings_lines(root, reg, p, project['targets'][p.get('key')]):
                    _line(out, 'warn', 'projects', text)
    if layout is not None:
        for d in layout['documents']:
            if d.get('name') and not d.get('motion_groups'):
                _line(out, 'warn', 'documents', 'document %s declares no "motion_groups": clearance.min_gap_moving checks nothing in it '
                                                '(declare which components move together, or [] stays a decision the report repeats)' % d.get('key'))
        try:                                            # units of the last run that worked longer than their budget: a defect of a probe
            import gate_plan
            for text in gate_plan.over_lines(gate_plan.scan_over_budget(root) + gate_plan.over_budget(gate_plan.load_plan(root))):
                _line(out, 'warn', 'units', text[len('OVER BUDGET  '):])
            if gate_plan.waiting_line(root):
                _line(out, 'warn', 'units', gate_plan.waiting_line(root))
        except Exception as e:
            _line(out, 'warn', 'units', 'the plan of the last run could not be read (%s)' % e)
        try:                                            # what the last scan said once per document: linked documents, equal bodies, parts in no motion group
            import gate_plan
            census = gate_common.load_census(root)
            keys = [d['key'] for d in layout['documents'] if d.get('key') and (census['docs'].get(d['key']) or {}).get('scanned')]
            for text in gate_plan.scan_notes(root, layout, census, keys):
                if 'declares no "motion_groups"' not in text:              # said just above
                    _line(out, 'warn', 'documents', text.split(None, 1)[1] if text.startswith(('LINKED', 'HINT')) else text[len('NO GROUP '):])
        except Exception as e:
            _line(out, 'warn', 'documents', 'the notes of the last scan could not be made (%s)' % e)
        try:                                            # "stl_names" against the last scan: the map takes effect with a scan
            for text in stl_name_lines(layout, gate_common.load_census(root), root):
                _line(out, 'warn', 'documents', text)
            for path, pids in sorted(gate_common.shared_stls(gate_common.load_census(root), layout).items()):
                _line(out, 'warn', 'documents', '%s is the STL of %s: an export of one overwrites the other, and `gate.py scan` stops with a NAME CLASH '
                                                'until one of them has another file' % (path, ' and '.join(pids)))
        except Exception as e:
            _line(out, 'warn', 'documents', 'the STL names of the last scan could not be compared with gate/layout.json (%s)' % e)
        try:
            loose = gate_common.unregistered(root, layout)
            for f in loose['projects']:
                _line(out, 'warn', 'projects', '%s is a .3mf of this project that gate/layout.json does not name: it is not verified '
                                               '(a save into it is only guarded in enforce mode)' % f)
            for folder in loose['stl_dirs']:
                _line(out, 'warn', 'projects', '%s/ holds STL files and is no "stl_dir" of gate/layout.json: its files are compared with no stamp' % folder)
            if not loose['complete']:
                _line(out, 'warn', 'projects', 'the project tree is too large to look through for unregistered files')
        except Exception as e:
            _line(out, 'warn', 'projects', 'the tree was not searched for unregistered files (%s)' % e)
        try:
            zips = gate_common.zip_writers(root)
            for f in zips['files']:
                _line(out, 'warn', 'writers', '%s writes a .3mf with zipfile itself: no guard, no ledger, no fit-setting comparison. Write it with '
                                              'bambu3mf.save(path, files, order) instead' % f)
            if not zips['complete']:
                _line(out, 'warn', 'writers', 'the project tree is too large to look through for .3mf writers that bypass bambu3mf.save')
            elif not zips['files']:
                _line(out, 'ok', 'writers', 'no script of the project writes a .3mf by itself: every write goes through bambu3mf.save')
        except Exception as e:
            _line(out, 'warn', 'writers', 'the tree was not searched for .3mf writers (%s)' % e)
        missing = gate_common.ask_rules_missing(root)
        if missing:
            _line(out, 'warn', 'settings', '.claude/settings.json lacks %s: `gate.py owner` refuses to write until the owner adds the rules of '
                                           'templates/gate/settings.json' % '; '.join(missing))
        else:
            _line(out, 'ok', 'settings', 'the ask rules for gate/owner and for `gate.py owner` are in .claude/settings.json')
    if reg is not None and resolved is not None:
        try:
            import gate_render
            state = gate_render.project_rules_state(root, reg)
            if state != 'current':
                _line(out, 'warn', 'RULES.md', 'gate/RULES.md is %s: `python3 gate/tools/gate_render.py --project` writes it' % state)
            else:
                _line(out, 'ok', 'RULES.md', 'gate/RULES.md is current')
        except GateError as e:
            _line(out, 'warn', 'RULES.md', str(e))
    if layout is not None and pinger is not False:
        _fusion_lines(out, layout, pinger)
    return out


def stl_name_lines(layout, census, root=None):
    """What doctor says about the STL file of each Fusion part, for every document that was scanned:
      - a key of "stl_names" that names no part of the last scan (neither a part name nor a part slug), and a slug key
        that is never read because the same part has an entry under its name;
      - a part whose mapped file is another part's (gate_common.stl_map_refusals): the scan refuses it and the part
        keeps its file, so "scan again" would not help: the map has to change;
      - a part whose census "stl" is not the path gate/layout.json gives now (gate_common.stl_of): the map or "stl_dir"
        changed after the scan, and every command still uses the path of the census until the next scan;
      - with `root`: a file in the STL folder whose name is the part's file in another letter case. On a file system
        that ignores case they are one file, and the name on disk stays the old spelling; a build table or a checkout
        on a file system that does not ignore case sees two names.
    Returns a list of texts."""
    out = []
    for d in layout.get('documents') or []:
        key = d.get('key')
        if not ((census.get('docs') or {}).get(key) or {}).get('scanned'):
            continue
        mine = {pid: e for pid, e in (census.get('parts') or {}).items()
                if isinstance(e, dict) and e.get('doc') == key and e.get('source', 'fusion') == 'fusion' and not e.get('gone')}
        name_of = {pid: mine[pid].get('name') or pid.split('/')[1] for pid in mine}
        by_name, by_slug = set(name_of.values()), {pid.split('/')[1]: name_of[pid] for pid in mine}
        names = d.get('stl_names') if isinstance(d.get('stl_names'), dict) else {}
        for k in sorted(names):
            if k not in by_name and k not in by_slug:
                out.append('"stl_names" of document %s: %r names no part of the last scan (a key is a part name exactly as the census shows it, '
                           'or its part slug); the file %s is the STL of no part' % (key, k, names[k]))
            elif k not in by_name and by_slug[k] in names:
                out.append('"stl_names" of document %s: the key %r is never read, the part has an entry under its name %r and the name is looked up '
                           'first: take one of the two out (%s is the STL of no part)' % (key, k, by_slug[k], names[k]))
        refused = gate_common.stl_map_refusals(d, name_of, census.get('parts') or {})
        listing = {}
        for pid in sorted(mine):
            have, want = mine[pid].get('stl'), gate_common.stl_of(d, name_of[pid])
            if pid in refused:
                out.append('%s: "stl_names" gives it the file %s, which is %s of the part %s: the scan refuses that (NAME CLASH) and the part keeps %s. '
                           'Change the map in gate/layout.json, then scan' % (pid, want.rsplit('/', 1)[1], 'the default STL name' if refused[pid][2] else 'the STL',
                                                                              refused[pid][1], have or 'no file'))
            elif have != want:
                out.append('%s: the census names %s as its STL, gate/layout.json gives %s now: scan again (`gate.py scan`), the change takes effect '
                           'with the scan' % (pid, have or 'no file', want or 'no file'))
            if root and want and pid not in refused:
                folder, base = want.rsplit('/', 1) if '/' in want else ('', want)
                if folder not in listing:
                    try:
                        listing[folder] = os.listdir(os.path.join(root, *folder.split('/')))
                    except OSError:
                        listing[folder] = []
                twins = sorted(n for n in listing[folder] if n != base and n.lower() == base.lower())
                if twins and base not in listing[folder]:
                    out.append('%s: its STL is %s, the folder holds %s: the same name in another letter case. A file system that ignores case keeps the '
                               'old spelling at the next export, one that does not sees two files: rename the file or change "stl_names" so that both '
                               'are spelled alike' % (pid, want, ', '.join(twins)))
    return out


def _settings_lines(root, reg, entry, target):
    """One line when a registered .3mf does not hold the fit settings its target declares (the judge of
    project.settings_match on the file as it is): doctor says it, `gate.py verify` is the check. [] when it does, when
    the registry is not loaded, or when the file cannot be read (verify says that)."""
    try:
        import gate_judges
        from probes import project_consistency
        value = (reg['params'].get('target.fit_settings') or {}).get('default') if reg else None
        path = os.path.join(root, *str(entry['file']).split('/'))
        facts = {'project': entry.get('key'), 'file': entry['file'], 'exists': True, 'target_declared': target,
                 'fit_settings': project_consistency.read_fit_settings(path, value)}
        ctx = {'part': '%s:' % entry.get('key'), 'rule': 'project.settings_match', 'tables': {}, 'target': target, 'census': None, 'prev': None, 'intent': None}
        found = gate_judges.JUDGES['project_settings_match'](facts, {'target.fit_settings': ' '.join(facts['fit_settings']['keys'])}, ctx)
        bad = [f for f in found if f.get('verdict') == 'FAIL']
        if not bad:
            return []
        return ['%s does not print with the fit settings its target declares: %s%s (`gate.py verify` is the check)'
                % (entry['file'], bad[0].get('text'), ' (+%d more)' % (len(bad) - 1) if len(bad) > 1 else '')]
    except Exception:
        return []


def _fusion_lines(out, layout, pinger):
    """Fusion answers one short ping; which registered documents are open is said by `gate.py scan`. Fusion not running
    is reported and is not an error of doctor."""
    try:
        import gate_fusion_rpc
        url = gate_fusion_rpc.endpoint(layout)
        ping = pinger or gate_fusion_rpc.http_ping
        try:
            up = bool(ping(url, 5))
        except NotImplementedError:
            _line(out, 'warn', 'fusion', 'the ping is not built yet')
            return
        if up:
            _line(out, 'ok', 'fusion', 'answers at %s; `gate.py scan` says which registered documents are open' % url)
        else:
            _line(out, 'warn', 'fusion', 'no answer at %s within 5 s: checks that need Fusion cannot run (files-only commands still work)' % url)
    except Exception as e:                                   # the ping is information, never the reason doctor fails
        _line(out, 'warn', 'fusion', 'not checked (%s)' % e)


def cmd_doctor(root, args, pinger=None):
    """gate.py doctor. root may be None (not gated: says so and checks only the interpreter).
    Prints one line per check: the interpreter (Python >= 3.9), the lock state (lock_state), the registry loads and the
    project parameters resolve, gate/project.json and gate/layout.json read, every registered document and .3mf exists
    or is named, Fusion answers one 5 s ping (gate_fusion_rpc.http_ping) and which registered documents are open.
    Returns 0 when nothing is wrong, 4 otherwise. Fusion not running is reported and is not an error of doctor."""
    lines = doctor_lines(root, pinger)
    for _, text in lines:
        print(text)
    errors = sum(1 for level, _ in lines if level == 'error')
    warns = sum(1 for level, _ in lines if level == 'warn')
    print('doctor: %d error(s), %d warning(s)' % (errors, warns))
    return EXIT_INFRA if errors else EXIT_PASS
