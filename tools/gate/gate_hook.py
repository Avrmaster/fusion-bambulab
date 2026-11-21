#!/usr/bin/env python3
"""The hooks of the gate. SessionStart prints the gate digest of the session's project, or nothing; the others are the
live checks of gate_watch.py.

    python3 gate_hook.py [session-start]     the digest, and what changed outside the tools / has no stamp
    python3 gate_hook.py subagent-start      the kept digest as context of a subagent (JSON)
    python3 gate_hook.py post-tool           a call that changed the model in Fusion: a dirty mark (PostToolUse, PostToolUseFailure)
    python3 gate_hook.py pre-tool            deny writes of the gate's state, unstamped STL/.3mf writes (enforce), unquoted owner calls
    python3 gate_hook.py stop | subagent-stop   block the end of a turn while changed parts are unchecked

Contract: it NEVER exits 2 and never takes more than about two seconds. No Fusion call, no judging. A decision is JSON on
stdout with exit 0 (a PreToolUse "deny", a Stop "block"). When a live check fails (an error of its own, or it takes
longer than LIVE_S): a PreToolUse call whose input names the gate's files, a printed file, the settings or an owner
command is DENIED with a line that says to call it again (what was not checked has not passed); every other call goes
on, with one systemMessage line that says the hook failed and nothing of that call was checked.
The session's project has no gate/project.json: exit 0, no output. Any error at all (a broken file, an unknown argument,
bad JSON on stdin): exit 0, no output. Arguments are read by hand, not with argparse (argparse exits 2).

The project folder is taken from the hook input on stdin (JSON with "cwd"), else from the environment variable
CLAUDE_PROJECT_DIR, else from the working directory; gate/project.json is looked for at that folder and above it.
What is printed on stdout becomes context of the session: the text of gate_report.digest(root).

Every gate command that can change the state of the gate keeps the digest in gate/run/digest.json with the state of its
inputs (gate_digest.py, gate_report.refresh_digest). The hook reads that file first and checks, by sizes and
modification times only, that it still holds: then it prints it, in milliseconds however large the project is. When
it no longer holds (or none is kept), the digest is computed now; when that takes longer than BUDGET_S (a project with
dozens of judged parts), the kept digest is printed with one line that says when it was built and what changed since.
Only with no kept digest at all is it left out, with one line.

The digest is computed by THIS copy of the gate (the plugin's), never by code of the project: a hook runs without a
prompt, so it must not run what a cloned folder brings along. When the project's vendored copy is forked or older than
the plugin, one more line says so; `python3 gate/tools/gate.py status` is then the project's own word.

Time: the work runs in helper threads (KEPT_S for the kept digest, BUDGET_S for computing it now). Nothing is printed for a folder that is not gated, however long the look took."""
import sys

EVENTS = ('session-start', 'subagent-start', 'pre-tool', 'post-tool', 'stop', 'subagent-stop')
LIVE_S = 3.0                    # for one live check (gate_watch): a check that takes longer is a failure (gate_hook.failed)
KEPT_S = 0.3                    # for reading the digest the gate keeps (gate_digest.py) and checking that it holds
BUDGET_S = 0.7                  # for computing the digest now; with STDIN_S and the interpreter start the hook stays near a second
STDIN_S = 0.2                   # a hook input that never ends must not hold the session
MAX_CHARS = 9000                # Claude Code keeps 10,000 characters of a hook's output
STATUS_COMMAND = 'python3 gate/tools/gate.py status'


def _gated_root(start):
    """Nearest folder at or above `start` with gate/project.json, or None. No import of the gate: a folder that is not
    gated costs nothing."""
    import os
    if not isinstance(start, str) or not start.strip():
        return None
    d = os.path.abspath(start)
    for _ in range(64):
        if os.path.isfile(os.path.join(d, 'gate', 'project.json')):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up
    return None


def find_project(stdin_text, environ, cwd):
    """The gated project root for this session, or None. stdin_text: the hook input (may be empty or not JSON);
    environ: os.environ; cwd: os.getcwd()."""
    import json
    candidates = []
    try:
        data = json.loads(stdin_text) if stdin_text and stdin_text.strip() else None
        if isinstance(data, dict):
            candidates.append(data.get('cwd'))
    except ValueError:
        pass
    candidates += [(environ or {}).get('CLAUDE_PROJECT_DIR'), cwd]
    for c in candidates:
        root = _gated_root(c)
        if root:
            return root
    return None


def _lock_line(root):
    """One line when the project's vendored gate is forked or older than this plugin; '' otherwise and on any error."""
    try:
        import gate_vendor
        st = gate_vendor.lock_state(root, gate_vendor.own_plugin_root())
        if st['state'] in ('forked', 'stale'):
            return 'gate: ' + gate_vendor.state_line(st) + '. This digest was computed by the plugin copy.'
    except Exception:
        pass
    return ''


def _here_on_path():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def kept(root):
    """(text, stale line or '') of the digest the gate keeps for `root` (gate_digest.py), or None when it keeps none.
    Only sizes and modification times are read: milliseconds, also in a project with many parts."""
    _here_on_path()
    import gate_digest
    rec = gate_digest.read(root)
    if rec is None:
        return None
    groups = gate_digest.changed(root, rec)
    return rec['text'].rstrip('\n'), (gate_digest.stale_line(rec, groups) if groups else '')


def output(root, text=None, note=''):
    """The text to print for a gated project: `text` (a kept digest), else gate_report.digest(root) computed now, with
    `note` under it. '' when there is nothing to say."""
    if text is None:
        _here_on_path()
        import gate_report
        text = gate_report.digest(root)
    text = (text or '').rstrip('\n')
    if not text:
        return ''
    lines = ['Print gate of %s (fusion-bambulab; skill: gate)' % root, text] + ([note] if note else [])
    lock = _lock_line(root)
    if lock:
        lines.append(lock)
    text = '\n'.join(lines)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit('\n', 1)[0] + '\n  ... (`%s` shows the rest)' % STATUS_COMMAND
    return text


def _within(fn, seconds):
    """(finished, value) of fn() run in a helper thread for at most `seconds`. An exception inside counts as finished
    with None. The thread is a daemon: a late one does not keep the process alive."""
    import threading
    box = {}

    def work():
        try:
            box['value'] = fn()
        except BaseException:
            box['value'] = None

    t = threading.Thread(target=work)
    t.daemon = True
    t.start()
    t.join(seconds)
    return (not t.is_alive()), box.get('value')


def _read_input(src):
    """The hook input as text: what arrived on `src` within STDIN_S. A stream that is never closed gives what it had sent
    by then; a terminal, a closed stream or any error gives ''."""
    try:
        if src is None or src.isatty():
            return ''
        try:
            fd = src.fileno()
        except Exception:
            fd = None
        if fd is None:                                         # a test hands in a text buffer
            done, got = _within(src.read, STDIN_S)
            return got if done and isinstance(got, str) else ''
        import os
        chunks = []

        def pump():
            while True:
                b = os.read(fd, 65536)
                if not b:
                    return
                chunks.append(b)

        _within(pump, STDIN_S)
        return b''.join(list(chunks)).decode('utf-8', 'replace')
    except BaseException:
        return ''


FAILED = object()                # live(): the check itself broke


def risky(text):
    """Does a PreToolUse input touch what the gate guards (its files, a printed file, the settings, an owner command)?
    Cheap: a look at the tool input's text only."""
    import json, re
    try:
        data = json.loads(text)
        ti = json.dumps(data.get('tool_input')) if isinstance(data, dict) else ''
    except Exception:
        return False
    return re.search(r'gate|\.3mf|\.stl|\.claude|ledger|bambu3mf|owner|27182|fusion_mcp|claude\s+plugin', ti, re.I) is not None


def live(event, text, environ):
    """The output of one live hook (gate_watch.py) for the hook input `text`; '' when there is nothing to say,
    FAILED when the check broke."""
    import json
    try:
        data = json.loads(text) if text and text.strip() else {}
    except ValueError:
        return ''
    if not isinstance(data, dict):
        return ''
    try:
        return _live(event, data, environ)
    except Exception:
        return FAILED


def _live(event, data, environ):
    _here_on_path()
    import gate_watch
    if event == 'pre-tool':
        return gate_watch.pre_tool(data, environ)
    if event == 'post-tool':
        return gate_watch.post_tool(data, environ)
    if event in ('stop', 'subagent-stop'):
        return gate_watch.stop(data, environ, subagent=event == 'subagent-stop')
    if event == 'subagent-start':
        root = gate_watch.session_root(data, environ)
        have = None
        if root:
            done, have = _within(lambda: kept(root), KEPT_S)
            have = have if done else None
        return gate_watch.subagent_start(data, environ, output(root, have[0], have[1]) if have else '')
    return ''


def failed(event, text, what):
    """The answer when a live check broke or ran out of time (see the contract at the top)."""
    import json
    if event == 'pre-tool' and risky(text):
        return json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason':
                           'print gate: the check of this call %s, and the call touches what the gate guards: call it again (what was not checked has '
                           'not passed). If it keeps failing, tell the owner.' % what}})
    if event == 'pre-tool' and not text.strip().startswith('{'):
        return ''
    return json.dumps({'systemMessage': 'print gate: the %s hook %s; nothing of this call was checked by it.' % (event, what)})


def project_lines(root):
    """gate_watch.project_lines(root), or [] on any trouble."""
    try:
        _here_on_path()
        import gate_watch
        return gate_watch.project_lines(root)
    except Exception:
        return []


def main(argv=None, stdin=None, stdout=None):
    """Run the hook. Always returns 0. Every exception is swallowed (the stub's NotImplementedError too)."""
    try:
        import os
        argv = list(sys.argv[1:] if argv is None else argv)
        if argv and (len(argv) != 1 or argv[0] not in EVENTS):
            return 0                                           # an unknown argument: say nothing, stop nothing
        text = _read_input(stdin or sys.stdin)
        if argv and argv[0] != 'session-start':
            done, out = _within(lambda: live(argv[0], text, dict(os.environ)), LIVE_S)
            if not done or out is FAILED or (done and out is None):
                out = failed(argv[0], text, 'took longer than %.0f s' % LIVE_S if not done else 'failed')
            if out:
                dst = stdout or sys.stdout
                dst.write(out.rstrip('\n') + '\n')
                dst.flush()
            return 0
        root = find_project(text, os.environ, os.getcwd())
        if not root:
            return 0
        done, have = _within(lambda: kept(root), KEPT_S)
        have = have if done else None
        if have is not None and not have[1]:
            out = output(root, have[0])                        # the digest the gate kept still holds: print it
        else:
            done, out = _within(lambda: output(root), BUDGET_S)
            if not done and have is not None:
                out = output(root, have[0], have[1])           # computing it now is too slow: the kept one, said to be stale
            elif not done:
                out = ('Print gate of %s (fusion-bambulab; skill: gate): the digest took longer than %.1f s and was left '
                       'out, and the gate keeps none yet (`%s` builds it). Run it before changing or exporting a part.' % (root, BUDGET_S, STATUS_COMMAND))
        more = project_lines(root)
        if more:
            out = (out.rstrip('\n') + '\n' if out else '') + '\n'.join(more)
        if out:
            dst = stdout or sys.stdout
            dst.write(out.rstrip('\n') + '\n')
            dst.flush()
    except BaseException:
        pass
    return 0


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        pass
    try:
        sys.stdout.flush()
    except BaseException:
        pass
    import os as _os
    _os._exit(0)                 # never 2, never a traceback; a helper thread that is still busy is not waited for
