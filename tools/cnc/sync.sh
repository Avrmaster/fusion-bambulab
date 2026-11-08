#!/bin/bash
# Rebuild the operator page data and mirror it to C:\cnc on the laptop:
#   station/CNC.html + station/jobs.js -> C:\cnc\,  jobs/ -> C:\cnc\jobs\  (jobs removed here are removed there)
# The open CNC.html on the laptop picks the new version up by itself within 4 s.
set -euo pipefail
cd "$(dirname "$0")"
/usr/bin/python3 station/build.py
IP=$(/usr/bin/python3 -c 'import json;print(json.load(open("laptop/laptop.json"))["ip"])')
WU=$(/usr/bin/python3 -c 'import json;print(json.load(open("laptop/laptop.json"))["user"])')
OPTS=(-i ~/.ssh/id_ed25519_cnc -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
      -o UserKnownHostsFile=~/.ssh/known_hosts_cnc -o ConnectTimeout=6 -o User="$WU")
ssh "${OPTS[@]}" "$IP" 'Remove-Item -Recurse -Force C:\cnc\.incoming -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force C:\cnc\.incoming | Out-Null'
scp -q -r "${OPTS[@]}" station/CNC.html station/jobs.js jobs "$IP:C:/cnc/.incoming/"
# robocopy exit codes below 8 mean success
ssh "${OPTS[@]}" "$IP" 'robocopy C:\cnc\.incoming C:\cnc /MIR /XD .incoming /NJH /NJS /NDL /NFL /NP | Out-Null; if ($LASTEXITCODE -ge 8) { exit $LASTEXITCODE }; Remove-Item -Recurse -Force C:\cnc\.incoming; exit 0'
echo "synced to $IP:C:\\cnc"
