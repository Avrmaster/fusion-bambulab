"""One Fusion unit = one script = one JSON-RPC call to the Fusion MCP HTTP endpoint (the way tools/fusion/fusion_mcp.py talks).

The script of a unit (build_script) is a small wrapper round the unit's text:

    # gate unit <id>: <probe> on <part>                      (ASCII only)
    import json
    _GATE_JOB = json.loads('<the job as JSON, as a Python string literal>')
    _GATE_UNIT = '<the unit as one Python string literal>'
    def run(_context=''):
        exec the unit in a PRIVATE dict {"JOB": the job}, call its run(), then rebind the global run to a stub

and the unit (unit_text) is

    import json
    <text of probes/_geom.py>
    <text of probes/_geom_rings.py>
    <text of probes/_fusion.py>          defines run(_context), which calls probe(JOB) and writes JOB["out"]
    <text of probes/<probe>.py>          defines probe(job)

Why the wrapper: the add-in keeps ONE namespace for the scripts of every session and calls whatever run() it finds
there. Sent bare, a unit would leave its run() and some 250 helper names behind: the next helper-only script of any
session would start the unit again (a census inside somebody's short call), and helpers like along() or JOB would
overwrite other sessions' globals. With the wrapper three names are left: json, and run / _gate_done as stubs that do
nothing (_GATE_JOB and _GATE_UNIT are removed when the unit has run).

It is sent as tools/call fusion_mcp_execute {"featureType": "script", "object": {"script": ..., "readOnly": true}}.
Every probe runs read-only except fusion_export, the one unit that writes a file besides its result (read_only_for).
The result is never read from the call's output: the script writes its envelope to JOB["out"] and the host reads that file.

JOB keys (new_job fills them):
    id            "u-0007"                     run   the run id
    probe         "fusion_inserts"             doc   the document NAME (never the active document)
    doc_key       key of the document in gate/layout.json
    part          "<doc key>/<slug>" or None (fusion_scan works on a document)
    component_id, occurrence, body, sig        how to find the part's body, and the signature the plan expects
    cursor        where the last call of this unit stopped, None at the start
    deadline_s    the budget of this call (default 20, never above 30)
    out           absolute path of the result file: <root>/gate/run/<run>/units/<id>.json
    params        flat parameter values (gate_params.values)
    tables        {"inserts": ..., "screws": ..., "bearings": ..., "washers": ...}
    target        the declared target or None
    features      the census holes and openings of the part ({"holes": [...], "openings": [...]}) or None before the census
    not_printed   regex list from gate/project.json
    motion_groups the document's motion groups from gate/layout.json
    legacy        {"insert_pockets": path or None, "fusion_parts": ..., "screw_access": ...} (tools/fusion)
    args          probe-specific extras ({} when none)
    sent          time.time() of the host when the call is sent (run_unit sets it): the unit's budget counts from here

Timeouts: HTTP timeout = deadline_s + 10. After a timeout: ONE ping of 5 s; no answer = FusionDown, the caller stops
(exit 4) and never sends a second call into a hung Fusion. The ping is a one-line read-only SCRIPT, not an MCP
initialize: the add-in's HTTP side can answer an initialize while Fusion's main thread hangs in a script, an autosave
or a mesh, and only a script that comes back shows that Fusion can run the next unit."""
import ast, http.client, json, os, socket, time, urllib.error, urllib.parse, urllib.request
from gate_common import GateError, fusion_tool_path, tools_dir

DEFAULT_URL = 'http://127.0.0.1:27182/mcp'
MAX_BUDGET_S = 30
HTTP_EXTRA_S = 10
PING_S = 5
PASTED = ('_geom.py', '_geom_rings.py', '_fusion.py')
JOB_LINE = '_GATE_JOB = json.loads('
JOB_KEYS = ('id', 'run', 'probe', 'doc', 'doc_key', 'part', 'component_id', 'occurrence', 'body', 'sig', 'cursor', 'deadline_s',
            'out', 'params', 'tables', 'target', 'features', 'not_printed', 'motion_groups', 'legacy', 'args')
UNIT_LINE = '_GATE_UNIT = '
PING_SCRIPT = "def run(_context=''):\n    pass\n"          # the liveness probe of the skill: one line, read-only, leaves a run() that does nothing
WRAPPER = '''

def run(_context=''):
    g = globals()
    job, unit = g.get('_GATE_JOB'), g.get('_GATE_UNIT')

    def _gate_done(_context=''):
        return None
    g['run'] = _gate_done                     # whatever happens below: a later call of run() by any session does nothing
    g.pop('_GATE_JOB', None)
    g.pop('_GATE_UNIT', None)
    if job is None or unit is None:
        return None
    private = {'__name__': 'gate_unit', 'JOB': job}
    exec(compile(unit, 'gate_unit_%s' % job.get('id'), 'exec'), private)
    return private['run'](_context)
'''
FUSION_PROBES = ('fusion_scan', 'fusion_census', 'fusion_inserts', 'fusion_bearings', 'fusion_openings', 'fusion_screws',
                 'fusion_gap', 'fusion_thin', 'fusion_near', 'fusion_export')
WRITING_PROBES = ('fusion_export',)            # every other unit is sent with readOnly true
CONNECT_S = 10                                 # the MCP initialize before the one call
ENVELOPE_KEYS = ('job', 'doc', 'doc_version', 'part', 'sig', 'probe', 'facts', 'done', 'cursor', 'elapsed_s', 'error', 'open_documents')
LOOPBACK = ('127.0.0.1', 'localhost', '::1')   # the only hosts a unit is sent to
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # Fusion is local: never through http_proxy


class FusionDown(GateError):
    """Fusion does not answer (no server, or a timeout followed by a dead ping). The run stops with exit 4."""


def probes_dir():
    return os.path.join(tools_dir(), 'probes')


def new_job(unit_id, run, probe, doc, doc_key, out, part=None, component_id=None, occurrence=None, body=None, sig=None,
            cursor=None, deadline_s=20, params=None, tables=None, target=None, features=None, not_printed=None,
            motion_groups=None, args=None):
    """A complete JOB dict: every key of JOB_KEYS, defaults filled, "legacy" looked up, deadline capped at MAX_BUDGET_S.
    `out` must be an absolute path. GateError for an unknown probe or a relative `out`."""
    if probe not in FUSION_PROBES:
        raise GateError('%r is not a Fusion probe' % (probe,))
    if not os.path.isabs(out):
        raise GateError('JOB["out"] must be an absolute path, got %r' % (out,))
    return {'id': unit_id, 'run': run, 'probe': probe, 'doc': doc, 'doc_key': doc_key, 'part': part, 'component_id': component_id,
            'occurrence': occurrence, 'body': body, 'sig': sig, 'cursor': cursor, 'deadline_s': min(float(deadline_s), MAX_BUDGET_S),
            'out': out, 'params': params or {}, 'tables': tables or {}, 'target': target, 'features': features,
            'not_printed': list(not_printed or []), 'motion_groups': list(motion_groups or []),
            'legacy': {n: fusion_tool_path(n) for n in ('insert_pockets', 'fusion_parts', 'screw_access')}, 'args': args or {}}


def _ascii(text):
    """Printable ASCII only, every other character as '?': the text goes into the comment line of a script, and a line
    break or another control character in a name would end that comment."""
    return ''.join(c if ' ' <= c <= '~' else '?' for c in str(text))


def unit_text(job):
    """The text of one unit as it runs inside the wrapper's private dict: `import json`, then _geom.py, _geom_rings.py,
    _fusion.py and the probe file named job["probe"]. It expects the global JOB (the wrapper puts it there).
    GateError when a file is missing."""
    parts = ['import json']
    for name in PASTED + (job['probe'] + '.py',):
        path = os.path.join(probes_dir(), name)
        if not os.path.isfile(path):
            raise GateError('probe file is missing: %s' % path)
        with open(path, encoding='utf-8') as f:
            parts.append('# ---- %s ----\n%s' % (name, f.read()))
    return '\n'.join(parts) + '\n'


def build_script(job):
    """The full text of the script for one unit (see the top): an ASCII comment line, the JOB line (it starts with
    JOB_LINE and holds the job as one Python string literal, so job_of() can read it back), the unit as one string
    literal (UNIT_LINE, read back by unit_of()), and the wrapper's run(). Every character outside ASCII is escaped in
    both literals, so no name can be damaged on the way. GateError when a file is missing or the job lacks a key of
    JOB_KEYS."""
    missing = [k for k in JOB_KEYS if k not in job]
    if missing:
        raise GateError('job lacks %s' % ', '.join(missing))
    return '\n'.join(['# gate unit %s: %s on %s' % (_ascii(job['id']), _ascii(job['probe']), _ascii(job['part'] or job['doc'])),
                      'import json', JOB_LINE + repr(json.dumps(job, sort_keys=True)) + ')',
                      UNIT_LINE + ascii(unit_text(job))]) + WRAPPER


def unit_of(script):
    """The unit text a script of build_script() carries (tests compile and run it). GateError when there is none."""
    for line in script.splitlines():
        if line.startswith(UNIT_LINE):
            try:
                return ast.literal_eval(line[len(UNIT_LINE):])
            except (ValueError, SyntaxError) as e:
                raise GateError('cannot read the unit line: %s' % e)
    raise GateError('the script has no unit line')


def job_of(script):
    """The JOB dict a script of build_script() carries (the fake transport of the tests uses it). GateError when the text
    has no JOB line."""
    for line in script.splitlines():
        if line.startswith(JOB_LINE) and line.endswith(')'):
            try:
                return json.loads(ast.literal_eval(line[len(JOB_LINE):-1]))
            except (ValueError, SyntaxError) as e:
                raise GateError('cannot read the JOB line: %s' % e)
    raise GateError('the script has no JOB line')


def merge_facts(old, new):
    """Facts of a unit that needed several calls: lists under the same key are concatenated, objects are merged key by
    key (the same rule inside), every other value is replaced by the newer one. `old` may be None. Returns a new dict."""
    out = dict(old or {})
    for k, v in (new or {}).items():
        if isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + v
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_facts(out[k], v)
        else:
            out[k] = v
    return out


def endpoint(layout):
    """The URL of the Fusion MCP endpoint: gate/layout.json "fusion"."endpoint", else DEFAULT_URL. Loopback http only:
    a unit carries project paths, document and part names. GateError for any other scheme or host."""
    url = ((layout or {}).get('fusion') or {}).get('endpoint') or DEFAULT_URL
    try:
        parts = urllib.parse.urlsplit(str(url))
        scheme, host = parts.scheme, parts.hostname
    except ValueError:
        scheme = host = None
    if scheme != 'http' or host not in LOOPBACK:
        raise GateError('gate/layout.json: "fusion"."endpoint" must be http://127.0.0.1:<port>/..., got %r (a unit carries project paths and names; '
                        'it is sent to this machine only)' % (url,))
    return url


def budget(layout):
    """Seconds one Fusion call may work: gate/layout.json "fusion"."budget_s" (default 20), capped at MAX_BUDGET_S."""
    return min(float(((layout or {}).get('fusion') or {}).get('budget_s') or 20), MAX_BUDGET_S)


def read_only_for(script):
    """Is the unit of this script sent read-only? True for every probe except those of WRITING_PROBES (fusion_export
    writes the quarantine STL). `script` is the text of build_script() or a JOB dict. A text without a JOB line is
    read-only."""
    if isinstance(script, dict):
        return script.get('probe') not in WRITING_PROBES
    try:
        return job_of(script).get('probe') not in WRITING_PROBES
    except GateError:
        return True


def request_body(script, read_only=True, request_id=2):
    """The JSON-RPC body of the ONE call of a unit: tools/call fusion_mcp_execute with
    {"featureType": "script", "object": {"script": script, "readOnly": read_only}}."""
    return {'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
            'params': {'name': 'fusion_mcp_execute', 'arguments': {'featureType': 'script', 'object': {'script': script, 'readOnly': bool(read_only)}}}}


def _post(url, body, sid, timeout_s):
    """One HTTP POST the way tools/fusion/fusion_mcp.py sends it. Returns (parsed answer or None, session id)."""
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    if sid:
        headers['Mcp-Session-Id'] = sid
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    with _OPENER.open(req, timeout=timeout_s) as r:
        raw = r.read().decode('utf-8', 'replace')
        sid = r.headers.get('Mcp-Session-Id') or sid
        if 'event-stream' in (r.headers.get('Content-Type') or ''):
            data = [line[5:].strip() for line in raw.splitlines() if line.startswith('data:')]
            raw = data[-1] if data else ''
    return (json.loads(raw) if raw.strip() else None), sid


def _connect(url, timeout_s):
    """MCP initialize + the initialized notification. Returns (answer of initialize, session id)."""
    res, sid = _post(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                           'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'fusion-bambulab-gate', 'version': '1'}}},
                     None, timeout_s)
    _post(url, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid, timeout_s)
    return res, sid


def _is_timeout(e):
    reason = getattr(e, 'reason', None)
    return isinstance(e, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError)) or 'timed out' in str(e).lower()


def _failure(e, connected):
    """The transport result for a network exception. Before the call was sent, everything that is not a timeout means
    "no server"; after it, a broken connection is an error of this call (the next unit finds out whether Fusion lives)."""
    text = '%s: %s' % (type(e).__name__, e)
    if _is_timeout(e):
        return {'ok': False, 'text': '', 'error': 'timeout (%s)' % text, 'timeout': True, 'down': False}
    return {'ok': False, 'text': '', 'error': text, 'timeout': False, 'down': not connected}


def http_transport(url, script, timeout_s, read_only=None):
    """The real transport: initialize, then ONE tools/call fusion_mcp_execute with {"featureType": "script", "object":
    {"script": script, "readOnly": True}}, as tools/fusion/fusion_mcp.py does (JSON or event-stream answer).
    read_only None = read_only_for(script): false only for the export unit.

    Returns {"ok": bool, "text": what the tool returned (not used for results), "error": text or None,
             "timeout": bool (the HTTP call ran into timeout_s), "down": bool (connection refused: no server)}.
    Never raises for a network problem."""
    if read_only is None:
        read_only = read_only_for(script)
    t0, connected = time.time(), False
    try:
        _, sid = _connect(url, min(float(timeout_s), CONNECT_S))
        connected = True
        res, _ = _post(url, request_body(script, read_only), sid, max(1.0, float(timeout_s) - (time.time() - t0)))
    except ValueError as e:                                      # an answer that is not JSON: something else listens there
        return {'ok': False, 'text': '', 'error': 'the endpoint answered something that is not JSON: %s' % e, 'timeout': False, 'down': not connected}
    except (OSError, http.client.HTTPException) as e:            # URLError and socket.timeout are OSError
        return _failure(e, connected)
    if not isinstance(res, dict):
        return {'ok': False, 'text': '', 'error': 'empty answer from the endpoint', 'timeout': False, 'down': False}
    if res.get('error'):
        return {'ok': False, 'text': '', 'error': json.dumps(res['error']), 'timeout': False, 'down': False}
    out = res.get('result') or {}
    text = '\n'.join(str(c.get('text', '[%s]' % c.get('type'))) for c in out.get('content') or [] if isinstance(c, dict))
    if out.get('isError'):
        return {'ok': False, 'text': text, 'error': text or 'the tool reported an error', 'timeout': False, 'down': False}
    return {'ok': True, 'text': text, 'error': None, 'timeout': False, 'down': False}


def http_ping(url, timeout_s=PING_S):
    """True when Fusion RUNS a script within timeout_s: PING_SCRIPT, one line, read-only, sent the way a unit is sent.
    An MCP initialize alone is not taken as an answer (the HTTP side of the add-in can give it while Fusion hangs).
    Never raises."""
    t0 = time.time()
    try:
        res, sid = _connect(url, timeout_s)
        if not (isinstance(res, dict) and 'result' in res):
            return False
        res, _ = _post(url, request_body(PING_SCRIPT, True, request_id=3), sid, max(0.5, float(timeout_s) - (time.time() - t0)))
    except Exception:
        return False
    return isinstance(res, dict) and 'result' in res and not (res.get('result') or {}).get('isError')


def _made_envelope(job, error):
    return {'job': job['id'], 'doc': job['doc'], 'doc_version': None, 'part': job['part'], 'sig': None, 'probe': job['probe'],
            'facts': {}, 'done': False, 'cursor': job['cursor'], 'elapsed_s': 0.0, 'error': error, 'open_documents': []}


def _read_envelope(job):
    """The envelope the script wrote for THIS job, or (None, why)."""
    path = job['out']
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, encoding='utf-8') as f:
            env = json.load(f)
    except (ValueError, OSError) as e:
        return None, 'the result file cannot be read: %s' % e
    if not isinstance(env, dict) or env.get('job') != job['id']:
        return None, 'the result file belongs to another job (%r)' % (env.get('job') if isinstance(env, dict) else None,)
    for k, v in _made_envelope(job, None).items():
        env.setdefault(k, v)
    seen = env.get('seen')
    if isinstance(seen, dict):                  # the names as they arrived in Fusion: a damaged name must not read as "not open"
        off = [k for k in ('doc', 'body', 'occurrence') if k in seen and seen[k] != job.get(k)]
        if off:
            env['error'] = 'names were changed on the way into Fusion (%s): sent %r, arrived %r; nothing of this unit is used' % (
                ', '.join(off), [job.get(k) for k in off], [seen[k] for k in off])
            env['facts'], env['done'] = {}, False
    if not isinstance(env.get('facts'), dict):
        env['facts'], env['done'] = {}, False
        env['error'] = env.get('error') or 'the probe returned no facts object'
    return env, None


def run_unit(job, url, transport=None, pinger=None):
    """Run one call of one unit and return its envelope.

    job        a JOB dict of new_job()
    url        endpoint(layout)
    transport  function (url, script, timeout_s) -> the dict of http_transport(); None = http_transport.
               Tests pass tests/fake_fusion.FakeFusion().transport
    pinger     function (url, timeout_s) -> bool; None = http_ping

    Removes an old result file, sends build_script(job) with timeout job["deadline_s"] + HTTP_EXTRA_S, then reads
    job["out"]. Returns the envelope {"job", "doc", "doc_version", "part", "sig", "probe", "facts", "done", "cursor",
    "elapsed_s", "error", "open_documents"}. When no file was written the envelope is made here with "done": False and
    "error" saying why ("no result file", the tool's error text, or "timeout").
    Which documents are open: every envelope says so in "open_documents"; `gate.py doctor` asks with a fusion_scan job
    whose args are {"limit": 0}.
    Raises FusionDown when there is no server, or when the call timed out and the one ping after it got no answer."""
    transport = transport or http_transport                 # looked up at call time: a test may replace the module's own
    pinger = pinger or http_ping
    job['sent'] = time.time()                               # the unit counts its budget from here (same machine, same clock)
    script = build_script(job)
    for old in (job['out'], job['out'] + '.tmp'):
        if os.path.exists(old):
            os.remove(old)
    os.makedirs(os.path.dirname(job['out']), exist_ok=True)
    res = transport(url, script, job['deadline_s'] + HTTP_EXTRA_S)
    if res.get('down'):
        raise FusionDown('Fusion does not answer at %s (%s). Is Fusion running, with Preferences > General > API > Fusion MCP Server on?'
                         % (url, res.get('error') or 'no server'))
    if res.get('timeout'):
        if not pinger(url, PING_S):                         # ONE ping; never a second call into a Fusion that hangs
            raise FusionDown('unit %s (%s on %s) got no answer within %.0f s and Fusion does not answer a %d s ping: stopped, nothing else was sent'
                             % (job['id'], job['probe'], job['part'] or job['doc'], job['deadline_s'] + HTTP_EXTRA_S, PING_S))
        env, _ = _read_envelope(job)                        # the script may have finished while the answer was late
        return env if env is not None else _made_envelope(job, 'timeout')
    env, why = _read_envelope(job)
    if env is None:
        return _made_envelope(job, why or res.get('error') or 'no result file')
    return env
