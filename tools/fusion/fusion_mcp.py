#!/usr/bin/env python3
"""Talk to the Fusion MCP server directly, for a session that has no fusion tools: an .mcp.json server connects only at session
start, so a Fusion started later stays invisible until a new session. Stdlib only.
    python3 fusion/tools/fusion_mcp.py ping
    python3 fusion/tools/fusion_mcp.py run <script.py> [--write]     runs the script read-only unless --write
    python3 fusion/tools/fusion_mcp.py call <tool> '<json arguments>'
FUSION_MCP_URL overrides http://127.0.0.1:27182/mcp."""
import json, os, sys, urllib.error, urllib.request
URL = os.environ.get('FUSION_MCP_URL', 'http://127.0.0.1:27182/mcp')

def post(body, sid=None, timeout=600):
    h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    if sid: h['Mcp-Session-Id'] = sid
    r = urllib.request.urlopen(urllib.request.Request(URL, json.dumps(body).encode(), h), timeout=timeout)
    raw = r.read().decode('utf-8', 'replace'); sid = r.headers.get('Mcp-Session-Id') or sid
    if 'event-stream' in (r.headers.get('Content-Type') or ''):
        data = [l[5:].strip() for l in raw.splitlines() if l.startswith('data:')]; raw = data[-1] if data else ''
    return (json.loads(raw) if raw.strip() else None), sid

def connect():
    res, sid = post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                     'clientInfo': {'name': 'fusion-bambulab', 'version': '1'}}}, timeout=20)
    post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, sid, timeout=20)
    return res, sid

def call(sid, tool, args):
    res, _ = post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}, sid)
    if 'error' in res: sys.exit(json.dumps(res['error']))
    out = res['result']
    for c in out.get('content', []):
        text = c.get('text', '[%s]' % c.get('type'))
        try: text = json.loads(text).get('message', text)      # scripts come back as {"message": printed output, "success": true}
        except (ValueError, AttributeError): pass
        print(text)
    if out.get('isError'): sys.exit(1)

def main():
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    try: res, sid = connect()
    except (urllib.error.URLError, OSError) as e:
        sys.exit('Fusion MCP does not answer at %s (%s). Is Fusion running, with Preferences > General > API > Fusion MCP Server on?' % (URL, e))
    if a[0] == 'ping': print('alive:', res['result'].get('serverInfo'))
    elif a[0] == 'run':
        call(sid, 'fusion_mcp_execute', {'featureType': 'script', 'object': {'script': open(a[1], encoding='utf-8').read(), 'readOnly': '--write' not in a}})
    elif a[0] == 'call': call(sid, a[1], json.loads(a[2]))
    else: sys.exit(__doc__)

if __name__ == '__main__': main()
