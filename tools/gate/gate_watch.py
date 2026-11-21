"""The live hooks of the gate: cheap checks around every tool call of a session, so that checking no longer depends on
the agent remembering the project's instructions. Standard library only; no Fusion call, no judge, no import of the rest
of the gate. Each one reads a few small files and answers in tens of milliseconds.

    gate_hook.py pre-tool        PreToolUse    deny: a write of the gate's own state, an STL or .3mf written past the
                                               gate (enforce mode; observe: a warning), an owner command whose quote is
                                               not the owner's own words; a dirty mark BEFORE a call that can change the
                                               model in Fusion (so a call that fails or hangs half way is marked too)
    gate_hook.py post-tool       PostToolUse / PostToolUseFailure   the mark, when the pre-tool hook did not leave one,
                                               and the line that says what to run
    gate_hook.py stop            Stop          the turn does not end while parts changed in Fusion are not checked
    gate_hook.py subagent-stop   SubagentStop  the same for a subagent
    gate_hook.py subagent-start  SubagentStart the kept digest goes to the subagent

Which gated project a call belongs to: the session's own (the hook input's cwd, else CLAUDE_PROJECT_DIR), and for a path
a call writes, the gated project that path lies in, whatever the session's folder is (a plugin-development session that
works on a gated project as an additional folder is guarded the same way). The hooks keep a list of the gated roots they
have seen (index_path(): ~/.claude/plugins/data/fusion-bambulab-gate/roots.json); a Fusion write of a session whose own
folder is not gated marks every root of that list, and its Stop checks them.

What counts as "the model changed in Fusion" (dirty): a call of the Fusion MCP tools fusion_mcp_execute (not readOnly)
or fusion_mcp_update, and a Bash command that sends a writing script to Fusion: `fusion_mcp.py run <script> --write`,
`fusion_mcp.py call fusion_mcp_execute|fusion_mcp_update ...` without "readOnly": true, anything that names the Fusion
MCP port 27182 and is no gate command, and a Python script file run by the command whose text names that port or those
tools. The gate's own calls (gate.py run, scan, export) never go through these tools and are never marks.

What counts as "checked": gate.py keeps the end of every `run` in gate/run/checks.jsonl ({"t", "ts", "started",
"command", "changed", "all", "parts", "target", "offline", "no_scan", "audit", "session", "exit"}). A dirty mark is
checked by a `run --changed` or `run --all` that STARTED after it, asked Fusion (not --offline, not --no-scan), is no
--audit, came from the same session (or from no session: the owner's own terminal) and ended with 0, 1 or 2 (exit 3 =
more calls needed; exit 4 = the gate could not run: the turn may end, with the line "NOT checked"). Any --for key will
do: `run --changed` selects every changed part whatever the key.

A stop is blocked at most STOP_CAP times in a row; then the turn ends with a loud message, and the marks it let go never
block again (one reminder line per later turn instead). A project whose vendored gate is older than these hooks (no
gate/tools/gate_watch.py: its gate.py keeps no checks.jsonl, so nothing could ever clear a mark) is never blocked: one
line says that its changes are NOT checked and how to update it. The owner can switch the hooks of his project in
gate/project.json: "hooks": {"stop": false} (no block, a line instead), "hooks": {"pre_tool": "warn"} (every denial
becomes a warning).

What counts as a write, for PreToolUse: the Write / Edit / MultiEdit / NotebookEdit tools by their path; in a Bash
command (split into its simple commands at ; & | && || and newlines, comments dropped, the bodies of if / for / while,
$( ) and backticks looked into, $VAR expanded from the command's own assignments and the environment): redirections,
tee, cp / mv / install / rsync / ln / ditto targets (a folder target: what lands in it), rm / touch / chmod / truncate,
find -delete and -exec, sed -i and perl -i, dd of=, curl -o, unzip / tar into a folder, the slicer CLI --export-3mf,
git checkout / restore / rm / mv / clean / reset --hard / stash / apply of a protected path (a folder or "." too),
`bash -c` and a here-document fed to a shell (both parsed again), and code an interpreter runs (-c, -e, a here-document
fed to it) that names a protected file (by its path, a path joined from pieces, or the name of a gate file in code that
names "gate") and writes on the same line (WRITE_MARKERS; output to stdout is no write), or writes a path handed to it
as an argument. Code that names one and writes on another line gets a warning, not a denial: it may only read it.

The owner quote check: `gate.py owner ...` passes only when --quote is the owner's own words: a whole owner turn (only
his newest turn, or one after the dry run of the bundle), or a piece of one of at least QUOTE_MIN characters and two
words. An owner turn is a transcript line with "type": "user", "origin": {"kind": "human"} and nothing else in it, a
"turnOrigin" of "human" (or none), not a sidechain, not meta, no tool result, with the text the host injects taken out;
or the answer the owner clicked in an AskUserQuestion card. Text typed by an agent, a workflow's computed task, a peer
session or a task notification never counts. `owner bundle` (not --dry-run) also needs a dry run of the same proposal,
unchanged since, and a quoted turn newer than that dry run. When the quote holds, the hook leaves a one-time ticket
(gate/run/owner_tickets.json: the exact arguments, the quote, the proposal's sha256); gate.py refuses an owner command
from an agent session without a matching ticket, so `python3 -m gate`, an import of gate.py or a script file that calls
it gets nowhere either.

A PreToolUse "deny" stops the call in every permission mode, bypassPermissions included: the host evaluates the hook
before its mode decides (an "ask" rule may not prompt in that mode; a hook's deny is not a prompt)."""
import calendar, fnmatch, hashlib, json, os, re, shlex, time, unicodedata

STOP_CAP = 3                      # blocks of a stop in a row before the turn is let go with a loud message
STOP_WINDOW_S = 600               # without stop_hook_active from the host: blocks within this many seconds are "in a row"
QUOTE_MIN = 12                    # characters of a quote that is a piece of a turn (with at least two words)
TICKET_S = 300                    # a ticket of an owner call is good for this long, once
DIRTY = ('run', 'dirty.jsonl')
CHECKS = ('run', 'checks.jsonl')
STATE = ('run', 'hook_state.json')
SAVES = ('run', 'saves.json')
TICKETS = ('run', 'owner_tickets.json')
TAIL_BYTES = 1 << 20              # the end of dirty.jsonl / checks.jsonl that is read: the recent lines
SCRIPT_BYTES = 512 << 10          # the most of a script file that is read
INDEX_ENV = 'FUSION_BAMBULAB_GATE_INDEX'     # where the list of gated roots is kept (tests point it at a scratch folder)
FUSION_TOOL = re.compile(r'^mcp__.*fusion.*__fusion_mcp_(execute|update)$')
FUSION_PORT = re.compile(r'\b27182\b')
STL_WRITERS = re.compile(r'MeshCalculator|exportManager|createSTLExportOptions|STLExport|open\([^)]*\.stl[\'"][^)]*[\'"][wa]b?[\'"]|\.stl[\'"]\s*,\s*[\'"][wa]b?[\'"]'
                         r'|(?i:export[\w.]*\s*\([^\n]*stl|stl[^\n]*\bexport)')
GATE_UNIT = re.compile(r'^# gate unit [^\n]*: \w+ on ', re.M)
INJECTED = re.compile(r'<(system-reminder|task-notification|cross-session-message|user-prompt-submit-hook|command-name|command-message|command-args|'
                      r'local-command-stdout|local-command-stderr|local-command-caveat|bash-stdout|bash-stderr|bash-input)\b[^>]*>.*?</\1\s*>', re.S)
# code an interpreter runs (python -c, node -e, a here-document fed to one) that writes files. Shell code is not
# matched by this: it is parsed for its targets (written_paths), so a "->" in a text is no write
WRITE_MARKERS = re.compile(r'open\([^)]*[\'"][wax]b?\+?[\'"]|write_text|write_bytes|json\.dump\(|\.write\(|shutil\.(copy|move|rmtree)|os\.(replace|rename|remove|unlink|truncate)|'
                           r'\.unlink\(|\bfs\.write|writeFileSync|copyFileSync|renameSync|unlinkSync|rmSync|'
                           r'\bwrite_jsonl?\(|\bappend_jsonl\(|_write_json\(|_append\(|_locked_update\(')     # the gate's own writers (gate_common)
TO_STDOUT = re.compile(r'sys\.std(?:out|err)\.write\(|json\.dump\((?=[^\n]*\bsys\.std(?:out|err)\b)')
HEREDOC = re.compile(r'<<-?[ \t]*([\'"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)(?:\n[ \t]*\2[ \t]*(?=\n|$)|$)', re.S)
INTERPRETERS = ('python', 'python3', 'python2', 'node', 'ruby', 'perl', 'php', 'osascript')
SHELLS = ('bash', 'sh', 'zsh', 'dash', 'ksh', 'fish')
WRITERS_ALL = ('rm', 'rmdir', 'unlink', 'truncate', 'touch', 'chmod', 'chown', 'tee', 'shred')
WRITERS_LAST = ('cp', 'install', 'rsync', 'ln', 'ditto', 'scp')
KEYWORDS = ('if', 'then', 'do', 'else', 'elif', 'while', 'until', '{', '}', '!', 'fi', 'done', 'function')
WRAPPERS = {'sudo': 'ugCDhpr', 'doas': 'u', 'env': 'uSC', 'command': '', 'builtin': '', 'exec': 'a', 'nice': 'n', 'nohup': '', 'time': '',
            'xargs': 'IiLlnPsdEa', 'caffeinate': 'tw', 'timeout': 'sk', 'gtimeout': 'sk', 'stdbuf': 'ioe', 'unbuffer': ''}
SLICERS = re.compile(r'(?i)bambu.?studio|orca.?slicer|prusa.?slicer|superslicer')
ASSIGN = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', re.S)
OWN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE_FILES = ('project.json', 'waivers.json', 'params.json', 'triage.json', 'bundles.json', 'events.jsonl', 'owner_tickets.json',
              'fit_settings.json', 'hook_state.json', 'dirty.jsonl', 'checks.jsonl', 'saves.json', 'census.json', 'LOCK.json')


# ---- small helpers ------------------------------------------------------------------------------------------------------

def _own_name():
    try:
        with open(os.path.join(OWN_ROOT, '.claude-plugin', 'plugin.json'), encoding='utf-8') as f:
            return json.load(f).get('name') or 'fusion-bambulab'
    except (OSError, ValueError, AttributeError):
        return 'fusion-bambulab'


OWN_NAME = _own_name()


def _plugin_template(d):
    """Is the folder `d` a template or a test fixture of THIS plugin's own tree (templates/gate/project.json, a fixture of
    its tests)? Only a folder under templates/ or tests/ of a plugin whose plugin.json names this plugin is none; any
    other .claude-plugin/plugin.json switches nothing off (writing one under a gated root is the gate's state)."""
    up = d
    for _ in range(4):
        pj = os.path.join(up, '.claude-plugin', 'plugin.json')
        if os.path.isfile(pj):
            rel = os.path.relpath(d, up).replace(os.sep, '/')
            if rel.split('/')[0] not in ('templates', 'tests'):
                return False
            data = _read_json(pj, {})
            return isinstance(data, dict) and data.get('name') == OWN_NAME
        nxt = os.path.dirname(up)
        if nxt == up:
            return False
        up = nxt
    return False


def gated_root(start):
    """Nearest folder at or above `start` with gate/project.json, or None. A template of the plugin's own tree is none
    (_plugin_template)."""
    if not isinstance(start, str) or not start.strip():
        return None
    d = os.path.abspath(os.path.expanduser(start))
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    for _ in range(64):
        if os.path.isfile(os.path.join(d, 'gate', 'project.json')):
            return None if _plugin_template(d) else d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up
    return None


def _find_gate(start):
    """What gate.py itself takes as the root from a working folder (gate_common.find_root: no template exemption)."""
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, 'gate', 'project.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def session_root(data, environ):
    """The gated root of the session: from the hook input's cwd, else CLAUDE_PROJECT_DIR."""
    for c in ((data or {}).get('cwd'), (environ or {}).get('CLAUDE_PROJECT_DIR')):
        r = gated_root(c)
        if r:
            return os.path.realpath(r)
    return None


def index_path(environ):
    """The file that lists the gated roots the hooks have seen, or None (no home folder in `environ`)."""
    e = environ or {}
    if e.get(INDEX_ENV):
        return e[INDEX_ENV]
    home = e.get('HOME')
    return os.path.join(home, '.claude', 'plugins', 'data', 'fusion-bambulab-gate', 'roots.json') if home else None


def known_roots(environ):
    """The gated roots of the list (those that still have gate/project.json), newest first."""
    p = index_path(environ)
    data = _read_json(p, {}) if p else {}
    roots = data.get('roots') if isinstance(data, dict) else None
    return [r for r in roots or [] if isinstance(r, str) and os.path.isfile(os.path.join(r, 'gate', 'project.json'))]


def remember_root(root, environ):
    """Put a gated root on the list (index_path). A write only when it is new. Never raises."""
    try:
        p = index_path(environ)
        if not p or not root:
            return
        data = _read_json(p, {})
        roots = [r for r in (data.get('roots') if isinstance(data, dict) else None) or [] if isinstance(r, str)]
        if root in roots:
            return
        _write_json(p, {'_about': 'The gated project roots the hooks of the print gate have seen (fusion-bambulab, gate_watch.py).', 'roots': [root] + roots[:63]})
    except Exception:
        pass


def _path(root, *parts):
    return os.path.join(root, 'gate', *parts)


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _append(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj, sort_keys=True) + '\n')


def _tail(path, size=TAIL_BYTES):
    """The JSON lines at the end of a file (the last `size` bytes); [] when it is missing."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - size))
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return []
    lines = data.splitlines()
    if n > size and lines:
        lines = lines[1:]                                   # a line cut in half
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _sha_file(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except (OSError, TypeError):
        return None


def mode_of(root):
    """"enforce" or "observe" (a file that cannot be read: read from its text; anything else counts as observe)."""
    try:
        with open(_path(root, 'project.json'), encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return 'observe'
    try:
        m = json.loads(text).get('mode')
        return m if m in ('observe', 'enforce') else 'observe'
    except (ValueError, AttributeError):
        return 'enforce' if re.search(r'"mode"\s*:\s*"enforce"', text) else 'observe'


def hook_switch(root, name):
    """The owner's switch of one hook in gate/project.json "hooks" (e.g. "stop": false, "pre_tool": "warn"), or None."""
    data = _read_json(_path(root, 'project.json'), {}) if root else {}
    hooks = data.get('hooks') if isinstance(data, dict) else None
    return hooks.get(name) if isinstance(hooks, dict) else None


def target_keys(root):
    """The keys of the declared targets (gate/project.json): what `run --for` takes."""
    try:
        t = _read_json(_path(root, 'project.json'), {}).get('targets') or {}
        return sorted(t) if isinstance(t, dict) else []
    except AttributeError:
        return []


def run_command(root, cwd=None):
    """The exact command that checks the changed parts; from a folder `cwd` outside the root it starts with cd root."""
    keys = target_keys(root)
    cd = ''
    if cwd and root and _rel_in(root, cwd) is None:
        cd = 'cd %s && ' % shlex.quote(root)
    if len(keys) == 1:
        return cd + 'python3 gate/tools/gate.py run --changed --for %s' % keys[0]
    if keys:
        return cd + 'python3 gate/tools/gate.py run --changed --for <KEY> (KEY: any of %s; --changed takes every changed part whatever the key)' % ', '.join(keys)
    return cd + 'python3 gate/tools/gate.py run --changed'


def can_record(root):
    """Does the project's vendored gate keep checks.jsonl (0.3.0 and later: gate/tools/gate_watch.py)? Without it no
    `run` could ever clear a mark."""
    return os.path.isfile(_path(root, 'tools', 'gate_watch.py'))


# ---- the shell: simple commands, nested ones too -------------------------------------------------------------------------

def _close(text, i):
    """The index of the ")" that closes the "$(" whose inside starts at `i` (quotes and nesting followed)."""
    depth, q = 1, None
    while i < len(text):
        c = text[i]
        if q:
            if c == '\\' and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in '\'"':
            q = c
        elif c == '\\':
            i += 2
            continue
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _scan(text):
    """(the text with comments and line continuations taken out and every $( ) and `...` replaced by one word, [the
    commands of those substitutions])."""
    out, subs, i, n, q = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if q == "'":
            out.append(c)
            q = None if c == "'" else q
            i += 1
            continue
        if c == '\\' and i + 1 < n:
            if text[i + 1] == '\n':
                i += 2                                      # a line continuation
                continue
            out.append(text[i:i + 2])
            i += 2
            continue
        if q is None:
            if c in '\'"':
                q = c
                out.append(c)
                i += 1
                continue
            if c == '#' and (i == 0 or text[i - 1] in ' \t\n;&|()'):
                j = text.find('\n', i)                      # a comment: to the end of the line
                i = n if j < 0 else j
                continue
            if c.isdigit() and (i == 0 or text[i - 1] in ' \t\n;&|()'):
                j = i
                while j < n and text[j].isdigit():
                    j += 1
                if j < n and text[j] in '<>':
                    i = j                                   # 2>/dev/null, 3<in: the number names a file descriptor, it is no word
                    continue
        elif c == '"':
            q = None
            out.append(c)
            i += 1
            continue
        if c == '$' and text.startswith('$(', i):
            j = _close(text, i + 2)
            subs.append(text[i + 2:j])
            out.append('_SUB_')
            i = j + 1
            continue
        if c == '`':
            j = text.find('`', i + 1)
            j = n if j < 0 else j
            subs.append(text[i + 1:j])
            out.append('_SUB_')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return ''.join(out), subs


def _words(cmd):
    """Tokens of a shell command with the operators and newlines apart (shlex with punctuation_chars), or None when it
    cannot be split."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=';&|<>()\n')
        lex.whitespace = ' \t\r'
        lex.whitespace_split = True
        lex.commenters = ''
        return list(lex)
    except ValueError:
        return None


def _simple(tokens):
    """[(argv, [(operator, target)])] per simple command."""
    cmds, argv, redir, i = [], [], [], 0
    while i < len(tokens):
        t = tokens[i]
        if t and set(t) <= set('<>&|') and '>' in t:
            if i + 1 < len(tokens):
                tgt = tokens[i + 1]
                if not (t.endswith('&') and tgt.isdigit()) and tgt != '-':
                    redir.append((t, tgt))
                i += 1
        elif t and '<' in t and set(t) <= set('<&|('):
            i += 1                                          # an input, or the word of a here-document: no write
        elif t and set(t) <= set(';&|()\n'):
            cmds.append((argv, redir))
            argv, redir = [], []
        else:
            argv.append(t)
        i += 1
    cmds.append((argv, redir))
    return [(a, r) for a, r in cmds if a or r]


def _expand(word, env, environ):
    """$NAME and ${NAME} of a word, from the command's own assignments, else the environment; unknown ones stay."""
    if '$' not in word:
        return word
    def sub(m):
        name = m.group(1) or m.group(2)
        if name in env:
            return env[name]
        if name in (environ or {}):
            return environ[name]
        return m.group(0)
    return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)', sub, word)


def _strip(argv, env):
    """argv without VAR=value in front (kept in `env`), shell keywords and wrappers (sudo, env, xargs, timeout, ...)
    with their options."""
    while argv:
        while argv and ASSIGN.match(argv[0]):
            m = ASSIGN.match(argv[0])
            env[m.group(1)] = m.group(2)
            argv = argv[1:]
        if not argv:
            break
        n = os.path.basename(argv[0])
        if n in KEYWORDS:
            argv = argv[1:]
            continue
        if n in WRAPPERS:
            vals = WRAPPERS[n]
            argv = argv[1:]
            while argv and argv[0].startswith('-') and argv[0] != '-':
                a = argv[0]
                argv = argv[1:]
                if a == '--':
                    break
                if len(a) == 2 and a[1] in vals and argv:
                    argv = argv[1:]
            if n in ('timeout', 'gtimeout') and argv:
                argv = argv[1:]                             # the duration
            continue
        break
    return argv


def _args(argv):
    return [a for a in argv[1:] if not a.startswith('-')]


def parse(cmd, cwd, environ=None, depth=0):
    """(commands, code, readable) of a shell text. commands: [(argv, [(operator, target)], folder)] of every simple
    command, those of bash -c, of a here-document fed to a shell, of $( ) and `...`, of find -exec and of the bodies of
    if / for / while too, with `cd` followed. code: [(text, [argument words], folder)] an interpreter runs (-c, -e, a
    here-document fed to it). readable: False when a part could not be split."""
    cmds, code, ok = [], [], True
    if depth > 3:
        return cmds, [(cmd, [], cwd)], False
    bare = HEREDOC.sub(lambda m: m.group(0)[:m.start(3) - m.start()], cmd)     # the bodies of here-documents are no shell words
    text, subs = _scan(bare)
    tokens = _words(text)
    if tokens is None:
        return cmds, [(cmd, [], cwd)], False
    here, env = cwd, {}
    pending = []
    for argv, redir in _simple(tokens):
        argv = _strip(list(argv), env)
        argv = [_expand(a, env, environ) for a in argv]
        redir = [(op, _expand(t, env, environ)) for op, t in redir]
        pending.append((argv, redir))
        while pending:
            argv, redir = pending.pop(0)
            if not argv and not redir:
                continue
            cmds.append((argv, redir, here))
            if not argv:
                continue
            name = os.path.basename(argv[0])
            rest = _args(argv)
            if name in ('cd', 'pushd'):
                here = os.path.join(here, os.path.expanduser(rest[0])) if rest else os.path.expanduser('~')
            elif name in SHELLS and '-c' in argv:
                i = argv.index('-c')
                if i + 1 < len(argv):
                    c2, k2, ok2 = parse(argv[i + 1], here, environ, depth + 1)
                    cmds += c2; code += k2; ok = ok and ok2
            elif name.rstrip('0123456789.') in INTERPRETERS or name in INTERPRETERS:
                for flag in ('-c', '-e', '-E', '--eval', '-p'):
                    if flag in argv:
                        i = argv.index(flag)
                        if i + 1 < len(argv):
                            code.append((argv[i + 1], argv[i + 2:], here))
            elif name == 'find':
                for flag in ('-exec', '-execdir', '-ok', '-okdir'):
                    if flag in argv:
                        sub = argv[argv.index(flag) + 1:]
                        sub = sub[:next((k for k, a in enumerate(sub) if a in (';', '+')), len(sub))]
                        if sub:
                            pending.append((_strip(sub, env), []))
    for s in subs:
        c2, k2, ok2 = parse(s, cwd, environ, depth + 1)
        cmds += c2; code += k2; ok = ok and ok2
    for m in HEREDOC.finditer(cmd):
        head = re.split(r'[;&|\n(`]', cmd[:m.start()])[-1].split()
        head = _strip(head, {})
        name = os.path.basename(head[0]) if head else ''
        if name in SHELLS:                                  # a script fed to a shell: its own commands
            c2, k2, ok2 = parse(m.group(3), here, environ, depth + 1)
            cmds += c2; code += k2; ok = ok and ok2
        elif name.rstrip('0123456789.') in INTERPRETERS or name in INTERPRETERS:
            code.append((m.group(3), head[1:], here))      # code fed to an interpreter; text fed to cat/tee is no code
    return cmds, code, ok


def _opt(argv, *names):
    """The value of the first of these options (-o VALUE, --output=VALUE), or None."""
    for k, a in enumerate(argv):
        for n in names:
            if a == n and k + 1 < len(argv):
                return argv[k + 1]
            if n.startswith('--') and a.startswith(n + '='):
                return a[len(n) + 1:]
    return None


def _patch_targets(path):
    """The files a patch writes (its "+++ b/..." lines), or None when it cannot be read."""
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read(SCRIPT_BYTES)
    except OSError:
        return None
    out = []
    for m in re.finditer(r'^\+\+\+ (?:b/)?([^\t\n]+)', text, re.M):
        if m.group(1).strip() != '/dev/null':
            out.append(m.group(1).strip())
    return out


def _git_paths(argv, here, join):
    """[(path, verb)] a git command writes in the work tree."""
    i, g_here = 1, here
    while i < len(argv) and argv[i].startswith('-'):
        if argv[i] == '-C' and i + 1 < len(argv):
            g_here = join(argv[i + 1]); i += 2; continue
        if argv[i] in ('-c', '--git-dir', '--work-tree', '--namespace') and i + 1 < len(argv):
            i += 2; continue
        i += 1
    if i >= len(argv):
        return []
    sub, sargs = argv[i], argv[i + 1:]
    gj = lambda p: os.path.join(g_here, os.path.expanduser(p))
    vals = {'-s', '--source', '-b', '-B', '--orphan', '-m', '--message', '-F', '--conflict', '--pathspec-from-file'}
    pos, k = [], 0
    while k < len(sargs):
        a = sargs[k]
        if a == '--':
            break
        if a in vals:
            k += 2; continue
        if not a.startswith('-'):
            pos.append(a)
        k += 1
    specs = sargs[sargs.index('--') + 1:] if '--' in sargs else None
    if sub == 'checkout':
        p = specs if specs is not None else (pos[1:] if len(pos) > 1 else [x for x in pos if x == '.'])
        return [(gj(x), 'tree') for x in p]
    if sub == 'restore':
        if ('--staged' in sargs or '-S' in sargs) and not ('--worktree' in sargs or '-W' in sargs):
            return []                                       # the index only
        return [(gj(x), 'tree') for x in (specs if specs is not None else pos)]
    if sub == 'reset':
        return [(g_here, 'tree')] if any(a in sargs for a in ('--hard', '--merge', '--keep')) else []
    if sub == 'stash':
        what = pos[0] if pos else 'push'
        return [] if what in ('list', 'show', 'create', 'drop', 'clear') else [(g_here, 'tree')]
    if sub == 'clean':
        if '-n' in sargs or '--dry-run' in sargs:
            return []
        return [(gj(x), 'remove') for x in ((specs if specs is not None else pos) or ['.'])]
    if sub in ('rm', 'mv'):
        return [(gj(x), 'remove' if sub == 'rm' else 'write') for x in (specs if specs is not None else pos)]
    if sub in ('apply', 'am'):
        if '--check' in sargs or '--stat' in sargs:
            return []
        out = []
        for p in pos:
            t = _patch_targets(gj(p))
            out += [(g_here, 'tree')] if t is None else [(gj(x), 'write') for x in t]
        return out
    return []


def _dest(name, argv, rest, join):
    """[(path, verb)] of what cp / mv / install / rsync / ln / ditto / scp write: a folder target takes what lands in it."""
    t_opt = _opt(argv, '-t', '--target-directory')
    if t_opt is not None:
        target, srcs = t_opt, [r for r in rest if r != t_opt]
    elif name == 'ln' and len(rest) == 1:
        target, srcs = '.', rest                            # `ln -s SRC`: the link lands in the folder the command runs in
    elif rest:
        target, srcs = rest[-1], rest[:-1]
    else:
        return []
    recursive = name in ('rsync', 'ditto', 'scp') or any(a in ('--recursive', '--archive') or (re.match(r'^-[a-zA-Z]+$', a) and set(a[1:]) & set('rRa'))
                                                         for a in argv[1:])
    tgt = join(target)
    if (os.path.isdir(tgt) or target.endswith('/') or target in ('.', '..') or t_opt is not None) and srcs:
        out = []
        for s in srcs:
            if name == 'rsync' and s.endswith('/'):
                out.append((tgt, 'tree'))
            else:
                out.append((os.path.join(tgt, os.path.basename(s.rstrip('/')) or '.'), 'tree' if recursive or os.path.isdir(join(s)) else 'write'))
        return out
    return [(tgt, 'tree' if recursive else 'write')]


def command_paths(cmds):
    """[(path, "write" | "remove" | "tree")] the simple commands of parse() touch, as far as their text shows them."""
    paths = []
    for argv, redir, here in cmds:
        join = lambda p, h=here: os.path.join(h, os.path.expanduser(p))
        for _, tgt in redir:
            paths.append((join(tgt), 'write'))
        if not argv:
            continue
        name = os.path.basename(argv[0])
        rest = _args(argv)
        if name in ('rm', 'rmdir', 'unlink', 'shred'):
            paths += [(join(p), 'remove') for p in rest]
        elif name in ('touch', 'truncate'):
            vals = ('-r', '--reference', '-d', '--date', '-t', '-s', '--size')     # a reference file, a date, a size: no target
            skip = {k + 1 for k, a in enumerate(argv) if a in vals}
            paths += [(join(a), 'write') for k, a in enumerate(argv) if k > 0 and k not in skip and not a.startswith('-')]
        elif name in WRITERS_ALL:
            paths += [(join(p), 'write') for p in rest]
        elif name == 'mv':
            t_opt = _opt(argv, '-t', '--target-directory')
            gone = [p for p in rest if p != t_opt] if t_opt is not None else rest[:-1]
            paths += [(join(p), 'remove') for p in gone] + _dest(name, argv, rest, join)
        elif name in WRITERS_LAST:
            paths += _dest(name, argv, rest, join)
        elif name == 'dd':
            paths += [(join(a[3:]), 'write') for a in argv[1:] if a.startswith('of=')]
        elif name in ('sed', 'gsed') and any(a.startswith('-i') or a == '--in-place' or a.startswith('--in-place=') for a in argv[1:]):
            paths += [(join(p), 'write') for p in rest]
        elif name == 'perl' and any(a == '-i' or a.startswith('-i') or (re.match(r'^-[a-zA-Z]*i', a) and not a.startswith(('-M', '-I', '-m', '-x', '-C'))) for a in argv[1:]):
            paths += [(join(p), 'write') for p in rest]
        elif name == 'find':
            if '-delete' in argv:
                paths += [(join(p), 'remove') for p in argv[1:next((k for k, a in enumerate(argv[1:], 1) if a.startswith(('-', '(', '!'))), len(argv))]]
        elif name in ('curl', 'wget'):
            o = _opt(argv, '-O', '--output-document') if name == 'wget' else _opt(argv, '-o', '--output')
            if o and o != '-':
                paths.append((join(o), 'write'))
        elif name == 'unzip' and not any(a in argv for a in ('-l', '-t', '-p', '-v', '-Z', '-z')):
            paths.append((join(_opt(argv, '-d') or '.'), 'tree'))
        elif name in ('tar', 'bsdtar', 'gtar') and any(a in ('-x', '--extract', '--get') or (re.match(r'^-[a-zA-Z]+$', a) and 'x' in a)
                                                       or (re.match(r'^[a-zA-Z]+$', a) and 'x' in a and k == 1) for k, a in enumerate(argv[1:], 1)):
            paths.append((join(_opt(argv, '-C', '--directory') or '.'), 'tree'))
        elif name == 'zip':
            if rest:
                paths.append((join(rest[0]), 'write'))
        elif name == 'git' and len(argv) > 1:
            paths += _git_paths(argv, here, join)
        elif SLICERS.search(name):
            o = _opt(argv, '--export-3mf', '--outputdir', '--export-gcode')
            if o:
                paths.append((join(o), 'write' if o.lower().endswith(('.3mf', '.gcode')) else 'tree'))
    return paths


def written_paths(cmd, cwd, environ=None):
    """([(path, verb)] a Bash command touches, the code an interpreter runs from -c / -e / a here-document, as text)."""
    cmds, code, _ = parse(cmd, cwd, environ)
    return command_paths(cmds), '\n'.join(c for c, _, _ in code if c)


def _joined(text):
    """The text with paths joined from pieces made one: os.path.join('gate', 'project.json') -> 'gate/project.json'."""
    joined = re.sub(r'([\'"])\s*\)?\s*(?:,|\+|/)\s*(?:[\'"]/[\'"]\s*\+\s*)?([\'"])', '/', text)
    return re.sub(r'/{2,}', '/', joined)


def _literals(p):
    """A regular expression of a whole string literal that names the protected path `p` (or a path under it)."""
    return re.compile(r'([\'"])([^\'"\n]*?(?<![\w.-])%s(?:/[^\'"\n]*)?)\1' % re.escape(p))


def _literal_use(text, p, root, here):
    """(writes, reads, elsewhere): how the whole string literals of `text` that name `p` use it. A literal resolves
    against `here` (the folder the code runs in): one that is another root's file (a scratch copy's gate/project.json)
    is "elsewhere". One in a read position is a read: open(X) or open(X, 'r'), the source of shutil.copy*(X, ...),
    read_json(X), Path(X).read_text(). Every other use counts as a write."""
    writes = reads = elsewhere = 0
    want = os.path.realpath(os.path.join(root, p))
    for m in _literals(p).finditer(text):
        lit = m.group(2)
        cut = os.path.expanduser(lit[:lit.index(p) + len(p)])
        full = cut if os.path.isabs(cut) else os.path.join(here or root, cut)
        if os.path.realpath(full) != want:
            elsewhere += 1
            continue
        before, after = text[:m.start()], text[m.end():]
        if (re.search(r'\bopen\(\s*$', before) and re.match(r'\s*(?:\)|,\s*[\'"]r[bt]?[\'"]\s*\))', after)
                or re.search(r'\b(?:shutil\.copy\w*|copyfile|read_json|read_jsonl)\(\s*$', before) and re.match(r'\s*[,)]', after)
                or re.search(r'\bPath\(\s*$', before) and re.match(r'\s*\)\s*\.(?:read_text|read_bytes)\(', after)):
            reads += 1
        else:
            writes += 1
    return writes, reads, elsewhere


def _mentions(text, root, here=None, writing=False):
    """Protected paths of `root` that a piece of code names: by its path relative to the root, its absolute path, a
    path joined from pieces (os.path.join('gate', 'project.json'), 'gate' + '/' + ..., Path('gate') / ...), or, in
    code that names "gate" at all, the name of one of the gate's own files. `writing`: the text is one line that writes;
    then a file named only by whole string literals that read it (open(X), the source of a copy) or that are another
    root's file is no hit."""
    joined = _joined(text)
    hits, left = [], text
    for p, tree in GATE_STATE + (('gate/layout.json', False),):
        if writing and p in text:
            w, _, _ = _literal_use(text, p, root, here)
            rest = _literals(p).sub('""', text)
            if not w and p not in rest and p not in _joined(rest):
                left = _literals(p).sub('""', left)
                continue                                    # named only where it is read, or as another root's file
        for spell in (p, os.path.join(root, p)):
            if spell in text or spell in joined:
                hits.append(p)
                break
    if not hits and re.search(r'[\'"/]gate[\'"/]', left):
        for b in GATE_FILES:
            if re.search(r'[\'"/]%s[\'"]' % re.escape(b), left):
                hits.append('gate/.../%s' % b)
    return hits


def _writes(line):
    """Does a line of code write a file (WRITE_MARKERS, with output to stdout and stderr left out)?"""
    return WRITE_MARKERS.search(TO_STDOUT.sub('', line)) is not None


# code that calls the owner command: "gate.py owner", "gate.py', 'owner'", "gate.py", "--root", ".", "owner", "-m gate owner"
OWNER_IN_CODE = re.compile(r'gate(?:\.py)?[\'"]?(?:\s*,?\s*[\'"]?--root[\'"]?\s*,?\s*[\'"]?[^\s\'",]+[\'"]?)?\s*,?\s*[\'"]?owner\b')


def _gate_index(argv):
    """The index in argv after which gate.py's own arguments start (gate.py, a glob of it, `-m gate`), or None."""
    for i, a in enumerate(argv):
        b = os.path.basename(a)
        if b == 'gate.py' or (any(c in b for c in '*?[') and fnmatch.fnmatchcase('gate.py', b)):
            return i
        if a == '-m' and i + 1 < len(argv) and argv[i + 1] in ('gate', 'gate.py'):
            return i + 1
    return None


def owner_calls(cmds, code):
    """([{"tail": argv from "owner" on, "quotes", "dry", "proposal", "help", "root_opt", "here", "bad"}], hidden): the owner
    commands of the parsed command. hidden: code an interpreter runs names gate and "owner" (the quote cannot be read)."""
    calls = []
    for argv, _, here in cmds:
        i = _gate_index(argv)
        if i is None:
            continue
        rest = argv[i + 1:]
        j, root_opt = 0, None
        while j < len(rest) and rest[j].startswith('-'):
            if rest[j] == '--root' and j + 1 < len(rest):
                root_opt = rest[j + 1]; j += 2; continue
            if rest[j].startswith('--root='):
                root_opt = rest[j][7:]
            j += 1
        if j >= len(rest) or rest[j] != 'owner':
            continue
        tail = rest[j:]
        call = {'tail': tail, 'quotes': [], 'dry': False, 'proposal': None, 'help': False, 'root_opt': root_opt, 'here': here, 'bad': None,
                'action': tail[1] if len(tail) > 1 else None}
        k, positional, ended = 1, [], False
        while k < len(tail):
            a = tail[k]
            if ended:
                positional.append(a)
            elif a == '--':
                ended = True
            elif a in ('-h', '--help'):
                call['help'] = True
            elif a == '--quote':
                if k + 1 < len(tail):
                    call['quotes'].append(tail[k + 1])
                k += 2
                continue
            elif a.startswith('--quote='):
                call['quotes'].append(a[len('--quote='):])
            elif a == '--dry-run':
                call['dry'] = True
            elif a.startswith('--q') or a.startswith('--d') and a != '--dry-run' and '--dry-run'.startswith(a):
                call['bad'] = 'write %s in full (an abbreviated option is not read the way the gate reads it)' % a
            elif a in ('--reason', '--note', '--part', '--tag', '--source', '--key', '--under', '--value') and k + 1 < len(tail):
                k += 2
                continue
            elif not a.startswith('-'):
                positional.append(a)
            k += 1
        if call['action'] == 'bundle':
            call['proposal'] = positional[1] if len(positional) > 1 else None
            if call['proposal'] is not None and os.path.basename(call['proposal']).startswith('-'):
                call['bad'] = 'a proposal path may not start with "-"'
        calls.append(call)
    hidden = any(OWNER_IN_CODE.search(c) or (re.search(r'\bgate\b', c) and re.search(r'[\'"]owner[\'"]', c)) for c, _, _ in code)
    return calls, hidden


def normalize(text):
    """For comparing a quote with a turn: NFC, curly quotes and dashes made plain, whitespace collapsed."""
    t = unicodedata.normalize('NFC', str(text))
    for a, b in (('\u2018', "'"), ('\u2019', "'"), ('\u201c', '"'), ('\u201d', '"'), ('\u00ab', '"'), ('\u00bb', '"'), ('\u2013', '-'), ('\u2014', '-'), ('\u00a0', ' ')):
        t = t.replace(a, b)
    return ' '.join(t.split())


def _bare(text):
    return re.sub(r'^[\W_]+|[\W_]+$', '', normalize(text).lower())


def _iso(ts):
    """Seconds of an ISO time of a transcript line ("2026-11-21T14:23:57.123Z"), or None."""
    if not isinstance(ts, str):
        return None
    m = re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?', ts)
    if not m:
        return None
    try:
        return calendar.timegm(time.strptime(m.group(1), '%Y-%m-%dT%H:%M:%S')) + (float(m.group(2)) if m.group(2) else 0.0)
    except ValueError:
        return None


def owner_turn_entries(transcript_path):
    """[{"text", "ts", "uuid"}] of the owner's own turns of a session transcript (see the top), oldest first. The whole
    file is read, line by line, and only lines that can be one are parsed. [] when it cannot be read."""
    out = []
    try:
        f = open(transcript_path, 'rb')
    except (OSError, TypeError):
        return []
    with f:
        for raw in f:
            if b'"human"' not in raw and b'"answers"' not in raw:
                continue
            try:
                e = json.loads(raw.decode('utf-8', 'replace'))
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get('type') != 'user' or e.get('isSidechain') or e.get('isMeta') or e.get('isCompactSummary'):
                continue
            tr = e.get('toolUseResult')
            if 'toolUseResult' in e:
                if isinstance(tr, dict) and isinstance(tr.get('answers'), dict) and isinstance(tr.get('questions'), list):
                    text = '\n'.join(str(v) for v in tr['answers'].values() if isinstance(v, (str, int, float)))    # the card the owner clicked
                    if text.strip():
                        out.append({'text': normalize(text), 'ts': _iso(e.get('timestamp')), 'uuid': e.get('uuid')})
                continue
            origin = e.get('origin')
            if not isinstance(origin, dict) or origin.get('kind') != 'human' or set(origin) - {'kind'}:
                continue
            if e.get('turnOrigin') not in (None, 'human'):
                continue
            content = (e.get('message') or {}).get('content') if isinstance(e.get('message'), dict) else None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = '\n'.join(b.get('text') for b in content if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str))
            else:
                continue
            text = INJECTED.sub(' ', text)
            if text.strip():
                out.append({'text': normalize(text), 'ts': _iso(e.get('timestamp')), 'uuid': e.get('uuid')})
    return out


def owner_turns(transcript_path):
    """The texts of the owner's own turns (owner_turn_entries), oldest first."""
    return [t['text'] for t in owner_turn_entries(transcript_path)]


def quote_turn(quote, transcript_path, after=None):
    """(the owner turn the quote comes from, or None; why not). A quote is a whole turn (his newest one, or with `after`
    one newer than that time), or a piece of a turn of at least QUOTE_MIN characters and two words (with `after`: of a
    turn newer than that)."""
    q = normalize(quote or '')
    if not q:
        return None, 'the quote is empty'
    turns = owner_turn_entries(transcript_path)
    if not turns:
        return None, 'no owner turn could be read from the session transcript'
    newest = turns[-1]
    piece = len(q) >= QUOTE_MIN and len(q.split()) >= 2
    seen = False
    for t in reversed(turns):
        late = after is None or (isinstance(t.get('ts'), (int, float)) and t['ts'] > after)
        whole = _bare(q) == _bare(t['text']) and _bare(q) != ''
        inside = q in t['text']
        seen = seen or whole or inside
        if not late:
            continue
        if whole and (t is newest or after is not None):
            return t, None
        if inside and piece:
            return t, None
    if seen and after is not None:
        return None, 'the owner said it before the dry run of this proposal was shown: his answer must come after it'
    if seen and not piece:
        return None, ('it is too short to stand for a decision: quote a whole turn (his newest one) or at least %d characters and two words of one' % QUOTE_MIN)
    return None, 'it does not appear in any of the owner\'s own turns of this session'


def quote_holds(quote, transcript_path):
    """Is `quote` the owner's own words (quote_turn)?"""
    return quote_turn(quote, transcript_path)[0] is not None


def _layout_kept(old_text, new_text):
    """Does a new gate/layout.json keep every registered project file, STL folder and document key of the old one?"""
    try:
        old, new = json.loads(old_text), json.loads(new_text)
    except (ValueError, TypeError):
        return False
    if not isinstance(old, dict):
        return True
    if not isinstance(new, dict):
        return False
    have = lambda d: ({('project', e.get('key'), e.get('file')) for e in d.get('projects') or [] if isinstance(e, dict)}
                      | {('stl_dir', e.get('stl_dir')) for k in ('documents', 'projects') for e in d.get(k) or [] if isinstance(e, dict) and e.get('stl_dir')}
                      | {('doc', e.get('key')) for e in d.get('documents') or [] if isinstance(e, dict)})
    return have(old) <= have(new)


HOOK_KEYS = ('hooks', 'disableAllHooks', 'allowManagedHooksOnly', 'enabledPlugins', 'permissions', 'defaultMode', 'skipDangerousModePermissionPrompt')


def _settings_kept(old_text, new_text):
    """Does a new settings file leave the hooks, the plugins and the permission rules alone? (Its other keys may change.)"""
    try:
        old = json.loads(old_text) if old_text.strip() else {}
        new = json.loads(new_text)
    except (ValueError, TypeError, AttributeError):
        return False
    if not isinstance(new, dict) or not isinstance(old, dict):
        return False
    return all(json.dumps(old.get(k), sort_keys=True) == json.dumps(new.get(k), sort_keys=True) for k in HOOK_KEYS)


def _new_text(tool, tool_input, path):
    """The text a Write / Edit / MultiEdit call leaves in `path`, or None when it cannot be told."""
    try:
        with open(path, encoding='utf-8') as f:
            old = f.read()
    except OSError:
        old = ''
    if tool == 'Write':
        return old, tool_input.get('content') if isinstance(tool_input.get('content'), str) else None
    edits = tool_input.get('edits') if tool == 'MultiEdit' else [tool_input]
    text = old
    for e in edits or []:
        if not isinstance(e, dict) or not isinstance(e.get('old_string'), str) or not isinstance(e.get('new_string'), str):
            return old, None
        if e['old_string'] not in text:
            return old, None
        text = text.replace(e['old_string'], e['new_string']) if e.get('replace_all') else text.replace(e['old_string'], e['new_string'], 1)
    return old, text


# ---- PreToolUse: what is protected -----------------------------------------------------------------------------------------

def _rel_in(root, path):
    """`path` relative to `root` with forward slashes, or None when it lies outside."""
    try:
        r = os.path.relpath(os.path.realpath(path), os.path.realpath(root)).replace(os.sep, '/')
    except ValueError:
        return None
    return None if r == '..' or r.startswith('../') else r


GATE_STATE = (('gate/owner', True), ('gate/project.json', False), ('gate/stamps', True), ('gate/tools', True), ('gate/rules', True),
              ('gate/LOCK.json', False), ('gate/fit_settings.json', False), ('gate/census.json', False), ('gate/facts', True), ('gate/attest', True),
              ('gate/run/dirty.jsonl', False), ('gate/run/checks.jsonl', False), ('gate/run/hook_state.json', False), ('gate/run/saves.json', False),
              ('gate/run/owner_tickets.json', False), ('gate/run/slice', True), ('gate/run/bed_faces.json', False), ('gate/run/placed_cache.json', False),
              ('bambulab/ledger', True), ('bambulab/tools/bambu3mf.py', False), ('bambulab/tools/ledger.py', False), ('bambulab/tools/meshid.py', False),
              ('.claude/settings.json', False), ('.claude/settings.local.json', False), ('.claude-plugin', True))
HOME_STATE = re.compile(r'\.claude/settings(\.local)?\.json|\.claude/projects/.+\.jsonl|\.claude/plugins(/[^/]+\.json|/.*%s.*)?|\.claude\.json'
                        r'|\.claude/managed-settings.*|Library/Application Support/ClaudeCode(/.*)?' % re.escape(OWN_NAME))


def _print_dirs(root):
    """The folders printed files of the project live in: every "stl_dir" and the folder of every registered .3mf."""
    layout = _read_json(_path(root, 'layout.json'), {})
    out = set()
    for k in ('documents', 'projects'):
        for e in (layout.get(k) if isinstance(layout, dict) else None) or []:
            if isinstance(e, dict):
                if isinstance(e.get('stl_dir'), str) and e['stl_dir']:
                    out.add(e['stl_dir'].strip('/'))
                if k == 'projects' and isinstance(e.get('file'), str) and '/' in e['file']:
                    out.add(os.path.dirname(e['file']).strip('/'))
    return out


def protected(path, session_root_=None, verb='write'):
    """Why a write of `path` is the gate's business, or None. (root, relative path, kind): kind "state" (the owner's
    files, the stamps, the vendored gate, the journal, the hook files, the settings and plugin files that hold the hooks,
    the session transcripts), "layout" (gate/layout.json: the registered projects), "print" (an .stl or .3mf of a gated
    project). With verb "remove" or "tree" a folder that holds any of them (gate/, bambulab/, the root itself) is
    "state" too; with "tree" a folder of printed files is "print". The home paths count when `session_root_` is given
    (a gated project is in play)."""
    if not isinstance(path, str) or not path:
        return None
    full = os.path.abspath(os.path.expanduser(path))
    if session_root_:
        for base in (os.path.expanduser('~'), '/'):
            r = _rel_in(base, full)
            if r is not None and HOME_STATE.fullmatch(r):
                return (session_root_, ('~/' if base != '/' else '/') + r, 'state')
    root = gated_root(full)
    if not root:
        return None
    r = _rel_in(root, full)
    if r is None:
        return None
    if r == 'gate/layout.json':
        return (root, r, 'layout')
    for p, tree in GATE_STATE:
        if r == p or (tree and r.startswith(p + '/')):
            return (root, r, 'state')
    if verb in ('remove', 'tree') and (r == '.' or any(p.startswith(r + '/') for p, _ in GATE_STATE + (('gate/layout.json', False),))):
        return (root, r, 'state')                            # rm -rf gate, mv bambulab elsewhere, git checkout . : the state goes with it
    if r.lower().endswith(('.stl', '.3mf')) and not r.startswith(('gate/', '.')):
        return (root, r, 'print')
    if verb == 'tree' and any(r == d or r.startswith(d + '/') or d.startswith(r + '/') for d in _print_dirs(root)):
        return (root, r, 'print')
    return None


def _deny(reason, context=None):
    out = {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}
    if context:
        out['additionalContext'] = context
    return json.dumps({'hookSpecificOutput': out})


def _warn(text):
    return json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': text}})


STATE_WHY = ('{rel} is the gate\'s own state (owner decisions, stamps, the vendored gate and the guarded writer, the print journal, the hooks, their '
             'settings and plugin files): an agent never writes it by hand. The owner\'s decisions go through `python3 gate/tools/gate.py owner ...` '
             'with his own words as --quote, or a proposal in gate/proposals/ he applies (`gate.py owner bundle`); the vendored files through '
             'setup_project.py --update-tools; STLs and stamps through `gate.py export`. A plugin or its hooks are switched off by the owner himself.')


def _where(root, rel):
    """A protected path as a denial names it: with its root, so that a command that touches two gated roots (a scratch copy
    of a project) says which one."""
    return rel if rel.startswith(('~/', '/')) or not root else os.path.join(root, rel)


def _remember_touched(root, own, cwd, environ):
    """Put a root a call writes in on the list only when it is the session's own or lies inside the session's folder: a
    scratch copy reached through a written path elsewhere does not join it."""
    if root == own or (cwd and _rel_in(cwd, root) is not None):
        remember_root(root, environ)


def _print_write(root, rel, how):
    """deny (enforce) or warn (observe) for an STL or .3mf written past the gate."""
    text = ('%s is a printed file of a gated project, written here %s. STLs are written by `python3 gate/tools/gate.py export <part> --for <key>` '
            '(it stamps them), .3mf files by bambu3mf.save() (it runs the guard); a .3mf taken back from git is rebuilt by the build script instead. ' % (_where(root, rel), how))
    if mode_of(root) == 'enforce':
        return _deny(text + 'Refused: gate/project.json says enforce.')
    return _warn('print gate (observe mode): ' + text + 'In enforce mode this call is refused.')


def _fusion_script(root, script, what, may_deny=True):
    """deny (enforce) / warn (observe) for a Fusion script that writes STL files, unless it is a unit of the gate.
    `may_deny` False (a session whose own folder is not gated: the root is only one the hooks know): a warning at most."""
    if not script or GATE_UNIT.search(script) or not STL_WRITERS.search(script):
        return ''
    text = ('%s writes STL files from inside Fusion (MeshCalculator / exportManager / an .stl opened for writing): such a file has no stamp and the '
            'next .3mf save is refused for it. Export through `python3 gate/tools/gate.py export <part> --for <key>`.' % what)
    if not may_deny:
        return _warn('print gate: if this writes an STL of the gated project %s: %s' % (root, text))
    if mode_of(root) == 'enforce':
        return _deny(text + ' Refused: gate/project.json says enforce.')
    return _warn('print gate (observe mode): ' + text + ' In enforce mode this call is refused.')


CLAUDE_OFF = re.compile(r'disableAllHooks|"hooks"|enabledPlugins|allowManagedHooksOnly')


def _claude_switch(cmds):
    """Why a `claude ...` command of the parse switches the hooks off, or None: plugin disable / uninstall /
    marketplace remove, a session started with --setting-sources, or with --settings that touch the hooks or plugins."""
    for argv, _, here in cmds:
        if not argv or os.path.basename(argv[0]) != 'claude':
            continue
        words = argv[1:]
        if words[:1] == ['plugin'] or words[:1] == ['plugins']:
            w = [a for a in words[1:] if not a.startswith('-')]
            if w[:1] and w[0] in ('disable', 'uninstall', 'remove', 'rm') and len(w) > 1 and w[1].split('@')[0] != OWN_NAME:
                continue                                    # another plugin: none of the gate's business
            if w[:1] and w[0] in ('disable', 'uninstall', 'remove', 'rm') or w[:2] in (['marketplace', 'remove'], ['marketplace', 'rm']):
                return '`claude plugin %s` switches the gate\'s hooks off' % ' '.join(w[:2])
        if any(a == '--setting-sources' or a.startswith('--setting-sources=') for a in words):
            return 'a session started with --setting-sources can leave out the settings that enable the gate\'s plugin'
        s = _opt(words, '--settings')
        if s is not None:
            text = s
            if not s.lstrip().startswith('{'):
                try:
                    with open(os.path.join(here, os.path.expanduser(s)), encoding='utf-8', errors='replace') as f:
                        text = f.read(SCRIPT_BYTES)
                except OSError:
                    return 'a session started with --settings from a file the gate cannot read'
            if CLAUDE_OFF.search(text):
                return 'a session started with --settings that change the hooks or the plugins'
    return None


def _script_files(cmds):
    """[(path, text)] of the script files the commands run with an interpreter (python3 x.py, node x.js)."""
    out = []
    for argv, _, here in cmds:
        if not argv:
            continue
        name = os.path.basename(argv[0])
        if not (name.rstrip('0123456789.') in INTERPRETERS or name in INTERPRETERS):
            continue
        if any(f in argv for f in ('-c', '-e', '-E', '--eval', '-m')):
            continue
        f = next((a for a in argv[1:] if not a.startswith('-')), None)
        if not f:
            continue
        p = os.path.join(here, os.path.expanduser(f))
        try:
            with open(p, encoding='utf-8', errors='replace') as fh:
                out.append((p, fh.read(SCRIPT_BYTES)))
        except OSError:
            pass
    return out


# ---- Fusion writes: the dirty marks -----------------------------------------------------------------------------------------

def _readonly(tool_input):
    obj = tool_input.get('object') if isinstance(tool_input, dict) else None
    return isinstance(obj, dict) and obj.get('readOnly') is True


def changes_fusion(tool_name, tool_input, cwd=None):
    """Does this call (as the hook input names it) send a writing script or an update to Fusion?"""
    if not isinstance(tool_name, str):
        return False
    m = FUSION_TOOL.match(tool_name)
    if m:
        return m.group(1) == 'update' or not _readonly(tool_input)
    if tool_name != 'Bash' or not isinstance(tool_input, dict):
        return False
    cmd = str(tool_input.get('command') or '')
    if re.search(r'fusion_mcp\.py\b[^|;&\n]*\brun\b[^|;&\n]*--write\b', cmd):
        return True
    call = re.search(r'fusion_mcp\.py\b[^|;&\n]*\bcall\s+fusion_mcp_(execute|update)\b([^|;&\n]*)', cmd)
    if call:
        return call.group(1) == 'update' or not re.search(r'"readOnly"\s*:\s*true', call.group(2))
    if FUSION_PORT.search(cmd) and not re.search(r'/ping\b|\bping\b', cmd):
        return not re.search(r'fusion_mcp_read\b|readOnly\\?"?\s*:\s*true', cmd)     # a read-only JSON-RPC call changes nothing
    if re.search(r'\.py\b', cmd) and cwd:
        try:
            cmds, _, _ = parse(cmd, cwd)
        except Exception:
            return False
        for p, text in _script_files(cmds):
            if os.path.basename(p) == 'gate.py' or 'fusion_mcp' in os.path.basename(p) or '/gate/tools/' in p.replace(os.sep, '/') or GATE_UNIT.search(text):
                continue
            if (FUSION_PORT.search(text) or re.search(r'fusion_mcp_(execute|update)', text)) and not re.search(r'readOnly["\']?\s*:\s*True|"readOnly"\s*:\s*true', text):
                return True
    return False


def _mark(root, data, when):
    _append(_path(root, *DIRTY), {'t': _now(), 'ts': time.time(), 'session': data.get('session_id'), 'agent': data.get('agent_id'),
                                  'tool': data.get('tool_name'), 'id': data.get('tool_use_id'), 'when': when})


def _marked(root, tool_use_id):
    return bool(tool_use_id) and any(m.get('id') == tool_use_id for m in _tail(_path(root, *DIRTY), 1 << 16))


def _roots_for(data, environ):
    """The roots a Fusion write of this session goes to: the session's own, else every root on the list."""
    r = session_root(data, environ)
    return [r] if r else known_roots(environ)


def post_tool(data, environ):
    """PostToolUse / PostToolUseFailure: the dirty mark of a call that changes the model in Fusion, unless the
    pre-tool hook left it already; and the line that says what to run. Returns the output text."""
    if not changes_fusion(data.get('tool_name'), data.get('tool_input'), data.get('cwd')):
        return ''
    roots = _roots_for(data, environ)
    if not roots:
        return ''
    for root in roots:
        if not _marked(root, data.get('tool_use_id')):
            _mark(root, data, 'post')
    event = data.get('hook_event_name') if data.get('hook_event_name') in ('PostToolUse', 'PostToolUseFailure') else 'PostToolUse'
    return json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext':
                       'print gate: this call changed (or may have changed) the model in Fusion. Before the turn ends: %s until it no longer exits 3, '
                       'then tell the owner every open row of the parts you changed.' % run_command(roots[0], data.get('cwd'))}})


# ---- Stop / SubagentStop ---------------------------------------------------------------------------------------------------

def _qualifies(c):
    return (isinstance(c, dict) and c.get('command') == 'run' and bool(c.get('changed') or c.get('all')) and not c.get('offline')
            and not c.get('no_scan') and not c.get('audit'))


def unchecked_marks(root, session, agent=None):
    """(dirty marks of this session, or of this subagent, that no qualifying run checked (_qualifies: started after the
    mark, asked Fusion, same session or none); the last qualifying run that started after the newest of them, or None)."""
    marks = [m for m in _tail(_path(root, *DIRTY)) if m.get('session') == session and (agent is None or m.get('agent') == agent)]
    if not marks:
        return [], None
    runs = [c for c in _tail(_path(root, *CHECKS)) if _qualifies(c) and isinstance(c.get('ts'), (int, float))
            and (c.get('session') in (None, '') or c.get('session') == session)]
    start = lambda c: c['started'] if isinstance(c.get('started'), (int, float)) else c['ts']
    done = max([start(c) for c in runs if c.get('exit') in (0, 1, 2)] or [0.0])
    left = [m for m in marks if isinstance(m.get('ts'), (int, float)) and m['ts'] > done]
    last = max((c for c in runs if left and start(c) > max(m['ts'] for m in left)), key=lambda c: c['ts'], default=None)
    return left, last


def _stop_root(root, data, environ, subagent, state):
    """The answer of the stop for one root: ('block', reason) | ('say', text) | (None, None)."""
    session = data.get('session_id')
    left, last = unchecked_marks(root, session, data.get('agent_id') if subagent else None)
    key = '%s/%s' % (session, data.get('agent_id') if subagent else '')
    blocks = state.setdefault('blocks', {})
    released = state.setdefault('released', {})
    gone = released.get(key) if isinstance(released.get(key), (int, float)) else 0.0
    if not left:
        blocks.pop(key, None)
        return None, None
    if last is not None and last.get('exit') == 4:
        blocks.pop(key, None)
        return 'say', ('print gate: parts were changed in Fusion and are NOT checked: the last `gate.py run` (%s) could not run '
                       '(exit 4: Fusion did not answer, or a gate file is broken). Say so to the owner; nothing of it is a pass.' % last.get('t'))
    if not can_record(root):
        return 'say', ('print gate: %d change(s) made in Fusion in %s are NOT checked, and this project\'s vendored gate is older than the hooks (it keeps no '
                       'gate/run/checks.jsonl, so no run can clear them): run setup_project.py %s --update-tools, then %s. Nothing of them is a pass.'
                       % (len(left), root, root, run_command(root, data.get('cwd'))))
    if hook_switch(root, 'stop') is False:
        return 'say', ('print gate: %d change(s) made in Fusion are NOT checked (the owner switched the Stop hook off in gate/project.json): %s'
                       % (len(left), run_command(root, data.get('cwd'))))
    fresh = [m for m in left if m['ts'] > gone]
    if not fresh:
        return 'say', ('print gate: %d change(s) made in Fusion in this session are still NOT checked (the reminders ran out earlier): %s'
                       % (len(left), run_command(root, data.get('cwd'))))
    entry = blocks.get(key) if isinstance(blocks.get(key), dict) else {}
    now = time.time()
    row = bool(data.get('stop_hook_active')) if 'stop_hook_active' in data else (now - float(entry.get('t') or 0) < STOP_WINDOW_S)
    n = int(entry.get('n') or 0) if row else 0
    if n >= STOP_CAP:
        blocks.pop(key, None)
        released[key] = max(m['ts'] for m in fresh)
        return 'say', ('print gate: %d change(s) made in Fusion in this session are NOT checked, and the turn ends anyway after %d reminders. '
                       'Nothing of them is a pass: %s' % (len(left), STOP_CAP, run_command(root, data.get('cwd'))))
    blocks[key] = {'n': n + 1, 't': now}
    more = ' The last run (%s) needs more calls (exit 3): call it again.' % last.get('t') if last is not None and last.get('exit') == 3 else ''
    return 'block', ('print gate: parts changed in Fusion in this %s (%d call(s), the last at %s) and are not checked. '
                     'Run %s until it no longer exits 3, then tell the owner every open row of the parts you changed '
                     '(a row marked inherited was there before). Exit 4 means the gate could not run: say "NOT checked".%s'
                     % ('subagent' if subagent else 'session', len(left), left[-1].get('t'), run_command(root, data.get('cwd')), more))


def stop(data, environ, subagent=False):
    """Stop / SubagentStop: block the end of the turn while changed parts are unchecked (see the top)."""
    own = session_root(data, environ)
    roots = [own] if own else known_roots(environ)
    said, reason = [], None
    for root in roots:
        state_path = _path(root, *STATE)
        state = _read_json(state_path, {})
        state = state if isinstance(state, dict) else {}
        before = json.dumps(state, sort_keys=True)
        what, text = _stop_root(root, data, environ, subagent, state)
        if what == 'block' and reason is None:
            reason = text
        elif what == 'say':
            said.append(text)
        if root == own and what != 'block':
            more = _unchecked_since(root, state, data.get('session_id'), subagent)
            said += more or []
        if json.dumps(state, sort_keys=True) != before:
            _write_json(state_path, state)
    if reason:
        return json.dumps({'decision': 'block', 'reason': reason})
    return json.dumps({'systemMessage': '\n'.join(said)}) if said else ''


def _save_ts(entry):
    """The time of a save of gate/run/saves.json as seconds, or None."""
    if isinstance(entry.get('ts'), (int, float)):
        return float(entry['ts'])
    try:
        return float(calendar.timegm(time.strptime(entry.get('time') or '', '%Y-%m-%dT%H:%M:%SZ')))
    except (TypeError, ValueError):
        return None


def _unchecked_since(root, state, session, subagent):
    """Lines "gate: N object(s) in <project> have no stamp: <names>" for the guarded saves since the last stop of this
    session (else since the first tool call the hooks saw of it) that let objects through without a stamp. Moves the
    session's mark on in `state`: returns None when nothing changed there, else the list of lines (maybe empty). The main
    session's Stop says them; a subagent's stop leaves them to the session."""
    if subagent or not isinstance(session, str) or not session:
        return None
    seen = state.setdefault('sessions', {})
    me = seen.get(session) if isinstance(seen.get(session), dict) else {}
    since = me.get('last_stop', me.get('started'))
    now = time.time()
    seen[session] = dict(me, last_stop=now)
    for k in [k for k, v in seen.items() if not isinstance(v, dict) or now - float(v.get('last_stop') or v.get('started') or 0) > 7 * 86400]:
        seen.pop(k, None)                                    # sessions of last week: gone
    if not isinstance(since, (int, float)):
        return []
    saves = _read_json(_path(root, *SAVES), {})
    saves = saves.get('projects') if isinstance(saves, dict) and isinstance(saves.get('projects'), dict) else {}
    out = []
    for f, e in sorted(saves.items()):
        if not isinstance(e, dict):
            continue
        t = _save_ts(e)
        names = [n for n in e.get('unchecked') or [] if isinstance(n, str)]
        if t is not None and t >= since and names:
            out.append('gate: %d object(s) in %s have no stamp: %s. Nothing of them is checked; export them through the gate (gate.py export PART) and swap '
                       'them in again' % (len(names), f, ', '.join(names[:12]) + (', ... (%d more)' % (len(names) - 12) if len(names) > 12 else '')))
    return out


def note_session(root, data):
    """The first tool call the hooks see of a session: its time goes into gate/run/hook_state.json (the Stop hook says
    the saves without a stamp from then on). Cheap: one small read; a write once per session."""
    session = data.get('session_id')
    if not isinstance(session, str) or not session or data.get('agent_id'):
        return
    path = _path(root, *STATE)
    state = _read_json(path, {})
    state = state if isinstance(state, dict) else {}
    seen = state.setdefault('sessions', {})
    if isinstance(seen, dict) and isinstance(seen.get(session), dict):
        return
    if not isinstance(seen, dict):
        state['sessions'] = seen = {}
    seen[session] = {'started': time.time()}
    try:
        _write_json(path, state)
    except OSError:
        pass


# ---- what changed outside the tools; what has no stamp (SessionStart, SubagentStart) ----------------------------------------

def project_lines(root, limit=12):
    """Lines about the registered .3mf files: a file whose size or modification time differs from what the last guarded
    save left (gate/run/saves.json: Bambu Studio saved it, or a file was copied over it), and the objects the guard let
    through without a stamp. Only sizes and times are read."""
    layout = _read_json(_path(root, 'layout.json'), {})
    saves = _read_json(_path(root, *SAVES), {})
    saves = saves.get('projects') if isinstance(saves, dict) and isinstance(saves.get('projects'), dict) else {}
    out, never = [], []
    for e in (layout.get('projects') or []) if isinstance(layout, dict) else []:
        f = e.get('file') if isinstance(e, dict) else None
        if not isinstance(f, str) or not f:
            continue
        full = f if os.path.isabs(f) else os.path.join(root, *f.split('/'))
        try:
            st = os.stat(full)
        except OSError:
            continue
        s = saves.get(f) or saves.get(os.path.relpath(os.path.realpath(full), root).replace(os.sep, '/'))
        if not isinstance(s, dict):
            never.append(f)
            continue
        if s.get('size') != st.st_size or s.get('mtime_ns') != st.st_mtime_ns:
            out.append('gate: %s changed outside the tools since its last guarded save (%s): Bambu Studio saved it, or a file was copied over it. '
                       'Run python3 gate/tools/gate.py verify' % (f, s.get('time')))
        names = [n for n in s.get('unchecked') or [] if isinstance(n, str)]
        if names:
            out.append('gate: %d object(s) in %s have no stamp: %s%s' % (len(names), f, ', '.join(names[:limit]), ', ... (%d more)' % (len(names) - limit) if len(names) > limit else ''))
    if never:
        out.append('gate: no guarded save of %s is on record yet (gate 0.3.0 keeps one at every save): what they hold is checked only by '
                   'python3 gate/tools/gate.py verify' % ', '.join(never))
    return out


def subagent_start(data, environ, digest_text):
    """SubagentStart: the digest the gate keeps and the project lines, as context of the subagent."""
    root = session_root(data, environ)
    if not root:
        return ''
    lines = [digest_text] if digest_text else ['Print gate of %s (fusion-bambulab; skill: gate): `python3 gate/tools/gate.py status` gives its state.' % root]
    lines += project_lines(root)
    lines.append('After a change of a part in Fusion: %s until it no longer exits 3, and report every open row.' % run_command(root))
    return json.dumps({'hookSpecificOutput': {'hookEventName': 'SubagentStart', 'additionalContext': '\n'.join(l for l in lines if l)[:9000]}})


# ---- PreToolUse ------------------------------------------------------------------------------------------------------------

def _owner_root(call):
    """The root gate.py will use for an owner call: --root, else the folder the command runs in."""
    if call.get('root_opt'):
        return os.path.realpath(os.path.join(call['here'], os.path.expanduser(call['root_opt'])))
    r = _find_gate(call['here'])
    return os.path.realpath(r) if r else None


def ticket_key(tail):
    """The key of a ticket: sha256 of gate.py's arguments from "owner" on (gate.py computes the same)."""
    return hashlib.sha256(json.dumps(list(tail)).encode('utf-8')).hexdigest()


def _give_ticket(root, call, data, turn, proposal_sha):
    path = _path(root, *TICKETS)
    book = _read_json(path, {})
    now = time.time()
    keep = [t for t in (book.get('tickets') if isinstance(book, dict) else None) or [] if isinstance(t, dict) and now - float(t.get('ts') or 0) < TICKET_S]
    keep.append({'key': ticket_key(call['tail']), 'quotes': [normalize(q) for q in call['quotes']], 'ts': now, 'session': data.get('session_id'),
                 'proposal_sha256': proposal_sha, 'turn': {'uuid': (turn or {}).get('uuid'), 'ts': (turn or {}).get('ts')}})
    _write_json(path, {'_about': 'One-time tickets of owner commands whose quote the PreToolUse hook found in the owner\'s own turns (gate_watch.py). gate.py owner '
                                 'takes one up; an agent never writes this file.', 'tickets': keep[-32:]})


def _dry_runs(root):
    st = _read_json(_path(root, *STATE), {})
    d = st.get('dry_runs') if isinstance(st, dict) else None
    return d if isinstance(d, dict) else {}


def _note_dry_run(root, proposal):
    sha = _sha_file(proposal)
    if not sha:
        return
    path = _path(root, *STATE)
    st = _read_json(path, {})
    st = st if isinstance(st, dict) else {}
    d = st.setdefault('dry_runs', {})
    d[os.path.realpath(proposal)] = {'sha256': sha, 'ts': time.time()}
    for k in sorted(d, key=lambda k: d[k].get('ts') or 0)[:-64]:
        d.pop(k, None)
    try:
        _write_json(path, st)
    except OSError:
        pass


def _owner_checks(calls, hidden, ok, cmd, data):
    """deny text for the owner calls of one command, or None; writes the tickets of those that hold."""
    if hidden or (not ok and re.search(r'gate', cmd) and re.search(r'\bowner\b', cmd)):
        return ('this command names gate and "owner" in a way the gate cannot read (code an interpreter runs, or a text that cannot be split): run '
                '`python3 gate/tools/gate.py owner ... --quote "<his words>"` as a plain command, so that the quote can be checked against the owner\'s own turns')
    grants = []
    for c in calls:
        if c['help']:
            continue
        root = _owner_root(c)
        if c['bad']:
            return '`gate.py owner %s`: %s' % (c['action'] or '', c['bad'])
        if c['action'] == 'bundle' and c['dry']:
            if root and c['proposal']:
                _note_dry_run(root, os.path.join(c['here'], os.path.expanduser(c['proposal'])))
            continue                                        # a dry run writes nothing: it shows the owner what his yes would change
        if c['action'] == 'table' and c['dry']:
            continue                                        # the same for one table row: gate.py stages it and prints the diff
        if not c['quotes']:
            return '`gate.py owner` needs --quote with the owner\'s own words from this session'
        after, sha = None, None
        if c['action'] == 'bundle':
            prop = os.path.join(c['here'], os.path.expanduser(c['proposal'] or ''))
            sha = _sha_file(prop) if c['proposal'] else None
            dry = _dry_runs(root).get(os.path.realpath(prop)) if root else None
            if not sha:
                return 'the proposal of this bundle cannot be read: name it by a path that exists'
            if not isinstance(dry, dict) or dry.get('sha256') != sha:
                return ('show the owner what this bundle changes first: `python3 gate/tools/gate.py owner bundle %s --dry-run` (the proposal %s since its last '
                        'dry run), then quote his answer to it word for word' % (c['proposal'], 'changed' if isinstance(dry, dict) else 'had no dry run'))
            after = dry.get('ts')
        turn = None
        for q in c['quotes']:
            turn, why = quote_turn(q, data.get('transcript_path'), after)
            if turn is None:
                return ('the --quote %r does not appear in any of the owner\'s own turns of this session as his decision: %s (text of agents, of a '
                        'workflow\'s task or of another session never counts). Ask the owner, then quote his answer word for word.' % (q[:200], why))
        if root is None:
            return 'the folder of this owner command is not gated (no gate/project.json at or above it, and no --root)'
        grants.append((root, c, turn, sha))
    for root, c, turn, sha in grants:
        try:
            _give_ticket(root, c, data, turn, sha)
        except OSError as e:
            return 'the ticket of this owner command could not be written (%s): nothing is applied without one' % e
    return None


def _pre_tool(data, environ):
    own = session_root(data, environ)
    tool, ti = data.get('tool_name'), data.get('tool_input') if isinstance(data.get('tool_input'), dict) else {}
    cwd = data.get('cwd') if isinstance(data.get('cwd'), str) and data.get('cwd') else (own or os.getcwd())
    if own:
        note_session(own, data)
        remember_root(own, environ)
    known = own or (known_roots(environ) or [None])[0]
    if changes_fusion(tool, ti, cwd):                     # the mark goes in BEFORE the call: a call that fails or hangs half way changed the model too
        for root in ([own] if own else known_roots(environ)):
            try:
                _mark(root, data, 'pre')
            except OSError:
                pass
    if isinstance(tool, str) and FUSION_TOOL.match(tool):
        obj = ti.get('object') if isinstance(ti.get('object'), dict) else {}
        script = obj.get('script') if isinstance(obj.get('script'), str) else ''
        return _fusion_script(known, script, 'this Fusion call', may_deny=bool(own)) if known else ''
    if tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        path = ti.get('file_path') or ti.get('notebook_path')
        if not isinstance(path, str) or not path:
            return ''
        full = os.path.abspath(os.path.expanduser(path if os.path.isabs(path) else os.path.join(cwd, path)))
        hit = protected(full, own)
        if not hit:
            return ''
        r, rel, kind = hit
        _remember_touched(r, own, cwd, environ)
        if kind == 'print':
            return _print_write(r, rel, 'with the %s tool' % tool)
        if kind == 'layout':
            old, new = _new_text(tool, ti, full)
            if new is not None and _layout_kept(old, new):
                return ''
            return _deny('%s: this edit removes a registered project, STL folder or document (or cannot be read as JSON). A project that is no '
                         'longer registered is not guarded: only the owner removes one (a proposal in gate/proposals/).' % _where(r, rel))
        if re.search(r'(^|/)\.claude/settings(\.local)?\.json$', rel):
            old, new = _new_text(tool, ti, full)
            if new is not None and _settings_kept(old, new):
                return ''
            return _deny('%s: this edit changes the hooks, the plugins or the permission rules (or cannot be read as JSON): they keep the print gate '
                         'running. Only the owner changes them, in his editor.' % _where(r, rel))
        return _deny(STATE_WHY.format(rel=_where(r, rel)))
    if tool != 'Bash':
        return ''
    cmd = str(ti.get('command') or '')
    cmds, code, ok = parse(cmd, cwd, environ)
    calls, hidden = owner_calls(cmds, code)
    why = _owner_checks(calls, hidden, ok, cmd, data)
    if why:
        return _deny(why)
    if own:
        off = _claude_switch(cmds)
        if off:
            return _deny('%s: the hooks keep the print gate running. Only the owner switches them off, in his own terminal.' % off)
    for p, verb in command_paths(cmds):
        hit = protected(p, own, verb)
        if not hit and '$' in p and known and re.search(r'(%s|\.stl|\.3mf)$' % '|'.join(re.escape(b) for b in GATE_FILES), p, re.I):
            return _deny('this command writes %s through a variable the gate cannot read, and the name is one of a gate file or a printed file: name '
                         'the path in full' % os.path.basename(p))
        if not hit:
            continue
        r, rel, kind = hit
        _remember_touched(r, own, cwd, environ)
        if kind == 'print':
            if verb == 'remove':
                continue                                    # a file that goes prints nothing wrong
            return _print_write(r, rel, 'by a shell command')
        if kind == 'layout':
            return _deny('%s is changed with the Edit tool, so that the change can be checked (a registered project may not go): '
                         'not by a shell command' % _where(r, rel))
        return _deny(STATE_WHY.format(rel=_where(r, rel)))
    for text, args, here in code:
        if not text or not any(_writes(l) for l in text.splitlines()):
            continue
        by_arg = any(_writes(l) and re.search(r'argv|ARGV|\$ARGV|process\.argv', l) for l in text.splitlines())
        for a in args:                                      # a path handed to the code: it writes what its arguments name
            hit = protected(os.path.join(here, os.path.expanduser(a)), own)
            if hit and hit[2] != 'print' and by_arg:
                return _deny('this command runs code that writes a path from its arguments and hands it %s. %s' % (hit[1], STATE_WHY.format(rel=hit[1])))
            if hit and hit[2] != 'print':
                return _warn('print gate: this command hands %s to code that writes files. If it writes %s, stop: %s' % (hit[1], hit[1], STATE_WHY.format(rel=hit[1])))
        roots = {gated_root(here), known} - {None}
        same = [h for root in roots for line in text.splitlines() if _writes(line) for h in _mentions(line, root, here, writing=True)]
        if same:                                            # one line names the file and writes: that is a write of it
            return _deny('this command runs code that writes %s. %s' % (', '.join(dict.fromkeys(same)), STATE_WHY.format(rel=same[0])))
        hits = [h for root in roots for h in _mentions(text, root)]
        if hits:                                            # it reads the gate's state and writes something else: said, not stopped
            return _warn('print gate: this command runs code that names %s and writes files. If it writes %s, stop: %s'
                         % (', '.join(dict.fromkeys(hits)), hits[0], STATE_WHY.format(rel=hits[0])))
    if known and re.search(r'\b(curl|lftp|ftp|sftp|wput)\b', cmd) and re.search(r'(-T\b|--upload-file|\bput\b|\bmput\b)', cmd) and re.search(r'ftps?://|:990\b|\bbambu', cmd, re.I):
        return _print_write(known, 'a file sent to the printer', 'by an upload')
    if known:
        for argv, _, here in cmds:
            if not argv or not re.search(r'fusion_mcp\.py$', argv[min(1, len(argv) - 1)]) and not re.search(r'fusion_mcp\.py$', argv[0]):
                continue
            k = next((k for k, a in enumerate(argv) if a == 'run'), None)
            if k is None or k + 1 >= len(argv):
                continue
            f = os.path.join(here, os.path.expanduser(argv[k + 1]))
            try:
                with open(f, encoding='utf-8', errors='replace') as fh:
                    out = _fusion_script(known, fh.read(SCRIPT_BYTES), 'the script %s' % argv[k + 1], may_deny=bool(own))
            except OSError:
                if own and mode_of(known) == 'enforce':
                    return _deny('the Fusion script %s cannot be read by the gate, so it cannot tell whether it writes STL files: name it by its full path' % argv[k + 1])
                out = ''
            if out:
                return out
    return ''


def pre_tool(data, environ):
    """PreToolUse (see the top). Returns the output text: '' lets the call go on. The owner's switch "pre_tool": "warn"
    of the session's project turns a denial into a warning."""
    out = _pre_tool(data, environ)
    if out and '"permissionDecision": "deny"' in out:
        own = session_root(data, environ)
        if own and hook_switch(own, 'pre_tool') == 'warn':
            reason = json.loads(out)['hookSpecificOutput']['permissionDecisionReason']
            return _warn('print gate (the owner switched denials to warnings in gate/project.json): ' + reason)
    return out


# ---- gate.py: the end of a run --------------------------------------------------------------------------------------------

def record_check(root, args, code, started=None, session=None):
    """Called by gate.py after `run`: keep its end in gate/run/checks.jsonl. Never raises. `session`: the agent session
    the run came from (CLAUDE_CODE_SESSION_ID), None for the owner's own terminal."""
    try:
        now = time.time()
        if session is None:
            session = os.environ.get('CLAUDE_CODE_SESSION_ID') or None
        _append(_path(root, *CHECKS), {'t': _now(), 'ts': now, 'started': float(started) if isinstance(started, (int, float)) else now,
                                       'command': 'run', 'changed': bool(getattr(args, 'changed', False)),
                                       'all': bool(getattr(args, 'all', False)), 'parts': list(getattr(args, 'part', None) or []),
                                       'target': getattr(args, 'target', None), 'offline': bool(getattr(args, 'offline', False)),
                                       'no_scan': bool(getattr(args, 'no_scan', False)), 'audit': bool(getattr(args, 'audit', False)),
                                       'session': session, 'exit': int(code)})
    except Exception:
        pass
