#!/bin/bash
# Run a PowerShell command on the CNC laptop, or open a shell there.
#   ./ssh.sh                       shell
#   ./ssh.sh 'Get-ChildItem C:\cnc'
# Address and user come from laptop/laptop.json, written by laptop/serve.py when setup.ps1 reports in.
set -euo pipefail
cd "$(dirname "$0")"
[ -f laptop/laptop.json ] || { echo "no laptop/laptop.json yet: run laptop/serve.py and setup.ps1 on the laptop" >&2; exit 1; }
IP=$(/usr/bin/python3 -c 'import json;print(json.load(open("laptop/laptop.json"))["ip"])')
WU=$(/usr/bin/python3 -c 'import json;print(json.load(open("laptop/laptop.json"))["user"])')
exec ssh -i ~/.ssh/id_ed25519_cnc -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=~/.ssh/known_hosts_cnc -o ConnectTimeout=6 -o User="$WU" "$IP" "$@"
