#!/bin/bash
# Read-only GRBL queries on the laptop's machine port (CH340, FTDI, CP210x, or the only COM port), while gSender is NOT connected (the port is exclusive).
#   ./grbl.sh            -> $I, $$, $#, $G, ?
#   ./grbl.sh '$$'       -> one query
# Only read-only queries are allowed: $$ $I $# $G $N ? . Anything that could move or change the machine is refused.
# Opening the port may reset an 8-bit controller (DTR), which only matters mid-job; gSender holding the port means "Access denied".
set -euo pipefail
cd "$(dirname "$0")"
Q=("$@"); [ ${#Q[@]} -eq 0 ] && Q=('$I' '$$' '$#' '$G' '?')
for q in "${Q[@]}"; do
  case "$q" in '$$'|'$I'|'$#'|'$G'|'$N'|'?') ;; *) echo "refused: $q (read-only queries only)" >&2; exit 2 ;; esac
done
LIST=$(printf "'%s'," "${Q[@]}"); LIST=${LIST%,}
PS=$(cat <<EOF
\$ErrorActionPreference = 'Stop'; \$ProgressPreference = 'SilentlyContinue'
\$names = @(Get-CimInstance Win32_PnPEntity | Where-Object { \$_.Name -match '\((COM\d+)\)' } | ForEach-Object Name)
\$pick = @(\$names | Where-Object { \$_ -match 'CH34\d|FTDI|CP210|USB.Serial|Arduino' }) + \$names | Select-Object -First 1
if (-not \$pick) { 'no COM port: machine off or USB unplugged'; exit 1 }
\$port = \$pick -replace '.*\((COM\d+)\).*','\$1'
\$p = New-Object System.IO.Ports.SerialPort \$port,115200,'None',8,'One'
\$p.DtrEnable = \$true
try { \$p.Open() } catch { "\$port busy (gSender connected?): \$_"; exit 1 }
Start-Sleep -Milliseconds 2200
\$banner = \$p.ReadExisting().Trim()
"## \$port  banner: \$banner"
foreach (\$q in @($LIST)) {
  if (\$q -eq '?') { \$p.Write('?') } else { \$p.Write(\$q + "\`n") }
  Start-Sleep -Milliseconds 900
  "## \$q"; \$p.ReadExisting().Trim()
}
\$p.Close()
EOF
)
ENC=$(printf '%s' "$PS" | iconv -f utf-8 -t utf-16le | base64 | tr -d '\n')
./ssh.sh "powershell -NoProfile -EncodedCommand $ENC"
