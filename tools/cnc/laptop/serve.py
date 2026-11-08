"""One-time bootstrap server on the Mac for the CNC laptop.

    /usr/bin/python3 cnc/laptop/serve.py          (Apple's python: the macOS firewall lets it accept LAN connections)

GET  /setup.ps1   setup.ps1 with this Mac's address and the ~/.ssh/id_ed25519_cnc public key filled in
POST /hello       the laptop's report; saved to cnc/laptop/laptop.json (ip, user, host, os, COM ports)
GET  /station/    the operator page, to preview it here
GET  /ping        "ok"
On the laptop, in PowerShell as Administrator:  irm http://<this mac>:8770/setup.ps1 | iex
"""
import http.server
import ipaddress
import json
import os
import socket
import subprocess
import time

PORT = 8770
HERE = os.path.dirname(os.path.abspath(__file__))
CNC = os.path.dirname(HERE)
KEY = os.path.expanduser('~/.ssh/id_ed25519_cnc.pub')


def mac_ip():
    for itf in ('en0', 'en1', 'en7'):
        ip = subprocess.run(['ipconfig', 'getifaddr', itf], capture_output=True, text=True).stdout.strip()
        if ip:
            return ip
    return socket.gethostbyname(socket.gethostname())


def pick_ip(ips, mine):
    net = ipaddress.ip_network(mine + '/24', strict=False)
    same = [i for i in ips if ipaddress.ip_address(i) in net]
    return (same or ips or [''])[0]


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=CNC, **k)

    def _send(self, body, ctype='text/plain; charset=utf-8', code=200):
        b = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == '/ping':
            return self._send('ok\n')
        if self.path == '/setup.ps1':
            s = open(os.path.join(HERE, 'setup.ps1'), encoding='utf-8').read()
            s = s.replace('__MAC__', '%s:%d' % (mac_ip(), PORT)).replace('__KEY__', open(KEY).read().strip())
            print(time.strftime('%H:%M:%S'), 'setup.ps1 ->', self.client_address[0], flush=True)
            return self._send(s)
        if self.path.startswith('/station'):
            return super().do_GET()
        if self.path.startswith('/jobs/'):
            return super().do_GET()
        self._send('not here\n', code=404)

    def do_POST(self):
        if self.path != '/hello':
            return self._send('not here\n', code=404)
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        d = json.loads(raw.decode('utf-8-sig'))
        ips = d.get('ips') or []
        d['ips'] = ips if isinstance(ips, list) else [ips]
        d['ip'] = pick_ip(d['ips'], mac_ip()) or self.client_address[0]
        d['seen'] = time.strftime('%Y-%m-%d %H:%M:%S')
        with open(os.path.join(HERE, 'laptop.json'), 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print(time.strftime('%H:%M:%S'), 'hello', json.dumps(d, ensure_ascii=False), flush=True)
        self._send('ok\n')

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    print('serving on http://%s:%d  (setup: irm http://%s:%d/setup.ps1 | iex)' % (mac_ip(), PORT, mac_ip(), PORT), flush=True)
    http.server.ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
