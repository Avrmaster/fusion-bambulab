#!/usr/bin/env python3
"""Run a Python job inside Autodesk Fusion from the host, through the Fusion MCP HTTP endpoint, and get its result back.
Standard library only. The export and the gate use it; a session needs no Fusion MCP tools for it.

    import fusion_rpc
    res = fusion_rpc.run_job(SOURCE, {'doc': 'Robot Arm V2', ...}, write=False, timeout=120)

SOURCE is the text of a Fusion-side module that defines job(JOB) -> dict (JSON-able). It is executed in a PRIVATE
namespace: the add-in keeps one namespace for the scripts of every session and calls whatever run() it finds there, so
a bare script would leave its helpers behind for the next session's call. Only a stub run() is left.
The result is never read from the call's output (print() inside Fusion can be lost, and a print on a broken add-in
stdout rolls the call back): job() returns a dict, the wrapper writes it to JOB['out'] and the host reads that file.
While the job runs, macOS is told that Fusion does work a person waits for (an NSProcessInfo activity): an idle
Fusion in the background is otherwise put into App Nap and runs 6 to 10 times slower (awake_begin/awake_end).

    python3 fusion_rpc.py ping        is Fusion there, and which documents are open

FUSION_MCP_URL overrides http://127.0.0.1:27182/mcp. The host is always local and never goes through a proxy."""
import json, os, sys, tempfile, time, urllib.error, urllib.request

URL = os.environ.get('FUSION_MCP_URL', 'http://127.0.0.1:27182/mcp')


class FusionError(Exception):
    """Fusion did not answer, or the job raised: the text says which, with the job's traceback."""


def _post(body, sid=None, timeout=600):
    h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    if sid: h['Mcp-Session-Id'] = sid
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    r = opener.open(urllib.request.Request(URL, json.dumps(body).encode(), h), timeout=timeout)
    raw = r.read().decode('utf-8', 'replace'); sid = r.headers.get('Mcp-Session-Id') or sid
    if 'event-stream' in (r.headers.get('Content-Type') or ''):
        data = [l[5:].strip() for l in raw.splitlines() if l.startswith('data:')]; raw = data[-1] if data else ''
    return (json.loads(raw) if raw.strip() else None), sid


def _connect():
    try:
        res, sid = _post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                          'clientInfo': {'name': 'print-engineering', 'version': '1'}}}, timeout=10)
        _post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid, timeout=10)
    except (urllib.error.URLError, OSError) as e:
        raise FusionError('Fusion MCP does not answer at %s (%s). Is Fusion running, with Preferences > General > API > '
                          'Fusion MCP Server on?' % (URL, e))
    return sid


# The Fusion side. %(source)r and %(job)r are filled in by run_job(); everything else is fixed text.
_WRAPPER = r'''
import json as _json

def run(_context=''):
    try:                                     # the add-in wraps sys.stdout once more on every call; past ~1000 every call
        import sys as _sys                   # fails with RecursionError in __getattr__: point the top wrapper at the base
        _b, _k = _sys.stdout, 0
        while hasattr(_b, '_original') and _k < 100000:
            _b, _k = _b._original, _k + 1
        if _k > 4:
            _sys.stdout._original = _b
    except Exception:
        pass
    g = globals()
    def _done(_context=''):
        return None
    g['run'] = _done                         # whatever happens below: a later call of run() by any session does nothing
    job = _json.loads(%(job)r)
    private = {'__name__': 'print_engineering_job', 'JOB': job}
    out = {'ok': False}
    awake = None
    try:
        exec(compile(%(awake)r, 'print_engineering_awake', 'exec'), private)
        awake = private['awake_begin']()
        exec(compile(%(source)r, %(name)r, 'exec'), private)
        t0 = __import__('time').time()
        out = {'ok': True, 'result': private['job'](job)}
        out['elapsed_s'] = round(__import__('time').time() - t0, 3)
    except BaseException as e:
        import traceback
        out = {'ok': False, 'error': '%%s: %%s' %% (type(e).__name__, e), 'traceback': traceback.format_exc()[-4000:]}
    finally:
        try: private['awake_end'](awake)
        except Exception: pass
    with open(job['out'], 'w', encoding='utf-8') as f:
        _json.dump(out, f)
    return None
'''

_AWAKE = r'''
import sys

def _objc():
    if sys.platform != 'darwin':
        return None
    import ctypes, ctypes.util
    path = ctypes.util.find_library('objc')
    if not path:
        return None
    objc = ctypes.cdll.LoadLibrary(path)
    for fn in (objc.objc_getClass, objc.sel_registerName):
        fn.restype, fn.argtypes = ctypes.c_void_p, [ctypes.c_char_p]
    send = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value

    def msg(obj, sel, *args):
        types = [ctypes.c_void_p, ctypes.c_void_p] + [type(a) if isinstance(a, ctypes.c_uint64) else ctypes.c_char_p if isinstance(a, bytes)
                                                      else ctypes.c_void_p for a in args]
        return ctypes.CFUNCTYPE(ctypes.c_void_p, *types)(send)(obj, objc.sel_registerName(sel), *args)
    return objc, msg, ctypes

def awake_begin(reason='print-engineering job'):
    """NSProcessInfo beginActivityWithOptions (user initiated, latency critical, idle system sleep allowed) for the length
    of one call: lifts App Nap at once. A handle for awake_end(), or None off macOS or when the call fails."""
    try:
        got = _objc()
        if got is None:
            return None
        objc, msg, ctypes = got
        info = msg(objc.objc_getClass(b'NSProcessInfo'), b'processInfo')
        text = msg(objc.objc_getClass(b'NSString'), b'stringWithUTF8String:', reason.encode('ascii', 'replace'))
        token = msg(info, b'beginActivityWithOptions:reason:', ctypes.c_uint64(0x00EFFFFF | 0xFF00000000), text)
        if not token:
            return None
        msg(token, b'retain')
        return (msg, info, token)
    except Exception:
        return None

def awake_end(handle):
    if not handle:
        return
    try:
        msg, info, token = handle
        msg(info, b'endActivity:', token)
        msg(token, b'release')
    except Exception:
        pass
'''


def run_job(source, job=None, write=False, timeout=300, name='job'):
    """Run `source` (defines job(JOB)) in Fusion with JOB = job + {"out": result file}. Returns what job() returned.
    write=False sends the call read-only (Fusion rolls back any change). Raises FusionError."""
    job = dict(job or {})
    fd, out = tempfile.mkstemp(prefix='fb_job_', suffix='.json', dir=_out_dir()); os.close(fd); os.remove(out)
    job['out'] = out
    script = _WRAPPER % {'job': json.dumps(job), 'source': source, 'awake': _AWAKE, 'name': 'print_engineering_%s' % name}
    sid = _connect()
    t0 = time.time()
    for attempt in range(3):
        try:
            res, _ = _post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'fusion_mcp_execute', 'arguments': {
                'featureType': 'script', 'object': {'script': script, 'readOnly': not write}}}}, sid, timeout=timeout)
        except (urllib.error.URLError, OSError) as e:
            raise FusionError('no answer from Fusion within %d s (%s): it may still be working on the job; wait, then ping' % (timeout, e))
        # the add-in's own stdout wrapper can fail before the job runs (RecursionError in __getattr__): the job did not
        # run, so sending it again is safe (the wrapper above repairs the stdout chain on the first call that gets through)
        if os.path.exists(out) or 'RecursionError' not in json.dumps(res or {}):
            break
    if not os.path.exists(out):
        text = json.dumps(res)[:2000] if res else 'no answer'
        raise FusionError('the job left no result (the call answered: %s)' % text)
    try:
        with open(out, encoding='utf-8') as f: data = json.load(f)
    finally:
        os.remove(out)
    if not data.get('ok'):
        raise FusionError('%s\n%s' % (data.get('error'), data.get('traceback', '')))
    r = data['result']
    if isinstance(r, dict):
        r.setdefault('_elapsed_s', data.get('elapsed_s')); r.setdefault('_call_s', round(time.time() - t0, 3))
    return r


def _out_dir():
    """A folder Fusion can write to: the macOS sandbox of the add-in reaches the user's home, not every temp folder."""
    d = os.environ.get('PRINT_ENGINEERING_RUN_DIR') or os.path.join(os.path.expanduser('~'), '.print-engineering', 'run')
    os.makedirs(d, exist_ok=True)
    return d


PING = r'''
import adsk.core
def job(JOB):
    app = adsk.core.Application.get()
    return {'documents': [d.name for d in app.documents], 'active': app.activeDocument.name if app.activeDocument else None,
            'version': app.version}
'''


def ping(timeout=15):
    return run_job(PING, {}, timeout=timeout, name='ping')


if __name__ == '__main__':
    if sys.argv[1:] == ['ping']:
        try: print(json.dumps(ping(), indent=1))
        except FusionError as e: sys.exit(str(e))
    else:
        sys.exit(__doc__)
