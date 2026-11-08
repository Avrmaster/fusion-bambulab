# One-time setup of the Windows laptop next to a GRBL CNC machine (gSender on USB).
# Run in PowerShell as Administrator, while serve.py runs on the Mac:
#     irm http://<mac-ip>:8770/setup.ps1 | iex
# serve.py fills in __MAC__ and __KEY__. Safe to run again: every step checks first.
#
# What it changes:
#   - network profile Public -> Private (LAN can reach SSH and gSender)
#   - power on the charger: no sleep, no hibernate, lid does nothing, USB never suspends
#   - Windows Update: no automatic restart while someone is logged in
#   - OpenSSH Server on, key-only login for the Mac (PowerShell as the shell)
#   - C:\cnc (jobs from the Mac), "CNC" shortcut on the desktop
#   - gSender 1.6.4 (if missing) + firewall rule for its remote mode, private networks only

& {  # a script block, so 'return' ends the setup also under iex
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Mac = '__MAC__'
$Key = '__KEY__'
$GsUrl = 'https://github.com/Sienci-Labs/gsender/releases/download/v1.6.4/gSender-1.6.4-Windows-64Bit.exe'
$Root = 'C:\cnc'

function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "   OK  $t" -ForegroundColor Green }
function Warn($t) { Write-Host "   !!  $t" -ForegroundColor Yellow }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host 'Run PowerShell as Administrator (right-click, "Run as administrator") and try again.' -ForegroundColor Red; return }

$report = [ordered]@{ host = $env:COMPUTERNAME; user = $env:USERNAME; warnings = @() }

Step 'Windows'
$os = Get-CimInstance Win32_OperatingSystem
$report.os = "$($os.Caption) $($os.Version) $($os.OSArchitecture)"
Ok $report.os
if (-not [Environment]::Is64BitOperatingSystem) { Warn 'gSender 1.6 needs 64-bit Windows'; $report.warnings += '32-bit' }

Step 'Network: Private'
try {
    Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Public' | ForEach-Object {
        Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private
    }
    Ok ((Get-NetConnectionProfile | ForEach-Object { "$($_.Name): $($_.NetworkCategory)" }) -join ', ')
} catch { Warn "Could not change the network profile: $_"; $report.warnings += 'network-profile' }

Step 'Power on the charger: no sleep, lid does nothing, USB never suspends'
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setactive SCHEME_CURRENT
Ok 'powercfg'

Step 'Windows Update: no automatic restart while logged in'
$au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
New-Item -Path $au -Force | Out-Null
New-ItemProperty -Path $au -Name NoAutoRebootWithLoggedOnUsers -Value 1 -PropertyType DWord -Force | Out-Null
Ok 'NoAutoRebootWithLoggedOnUsers = 1'

Step 'OpenSSH Server (Mac key only)'
$cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' | Select-Object -First 1
if (-not $cap) { Warn 'This Windows has no OpenSSH Server (needs Windows 10 1809 or newer)'; $report.warnings += 'no-openssh' }
else { try {
    if ($cap.State -ne 'Installed') { Write-Host '   installing, this can take a few minutes...'; Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
    if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    }
    New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
    New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null
    # admins log in with administrators_authorized_keys; the per-user file covers a non-admin login
    $files = @("$env:ProgramData\ssh\administrators_authorized_keys", "$env:USERPROFILE\.ssh\authorized_keys")
    foreach ($f in $files) {
        New-Item -ItemType Directory -Path (Split-Path $f) -Force | Out-Null
        if (-not (Test-Path $f) -or -not (Select-String -Path $f -SimpleMatch $Key -Quiet)) { Add-Content -Path $f -Value $Key -Encoding ascii }
    }
    # group names are localized, so SIDs: Administrators, SYSTEM
    icacls $files[0] /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
    Restart-Service sshd
    Ok "sshd $((Get-Service sshd).Status), Mac key added"
} catch { Warn "OpenSSH setup failed: $_"; $report.warnings += 'openssh-failed' } }

Step 'Folder C:\cnc and a CNC shortcut on the desktop'
New-Item -ItemType Directory -Path "$Root\jobs" -Force | Out-Null
if (-not (Test-Path "$Root\CNC.html")) {
    Set-Content -Path "$Root\CNC.html" -Encoding UTF8 -Value '<meta charset="utf-8"><body style="font:20px sans-serif;padding:40px">Waiting for the first jobs from the Mac...</body>'
}
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:PUBLIC\Desktop\CNC.lnk")
$lnk.TargetPath = "$Root\CNC.html"
$lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,12"
$lnk.Save()
Ok "$Root, CNC shortcut"

Step 'gSender'
$gsPaths = @("$env:LOCALAPPDATA\Programs\gSender\gSender.exe", "$env:ProgramFiles\gSender\gSender.exe")
$gs = $gsPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $gs) {
    $exe = "$env:TEMP\gSender-1.6.4-setup.exe"
    Write-Host '   downloading gSender 1.6.4 (146 MB) from Sienci Labs on GitHub...'
    Invoke-WebRequest $GsUrl -OutFile $exe -UseBasicParsing
    Start-Process $exe -ArgumentList '/S' -Wait
    $gs = $gsPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($gs) {
    Get-NetFirewallRule -DisplayName 'gSender (remote mode)' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName 'gSender (remote mode)' -Direction Inbound -Program $gs -Action Allow -Profile Private | Out-Null
    Ok $gs
    $report.gsender = $gs
} else { Warn ("gSender did not install, run the installer by hand: " + $exe); $report.warnings += 'gsender-missing' }

Step 'Machine USB port'
$ports = @(Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\(COM\d+\)' } | ForEach-Object Name)
$report.ports = $ports
if ($ports | Where-Object { $_ -match 'CH340|CH341' }) { Ok ($ports -join ', ') }
elseif ($ports) { Warn ("COM ports, but no CH340: " + ($ports -join ', ') + '. Fine for FTDI or CP210x controllers.') }
else { Warn 'No machine port yet. That is fine: switch the machine on and plug USB in later. If no port ever shows up, a CH340 controller needs the driver from wch-ic.com (CH341SER.EXE).' }

$report.ips = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | ForEach-Object IPAddress)
Step 'Reporting to the Mac'
try {
    $body = [Text.Encoding]::UTF8.GetBytes(($report | ConvertTo-Json))
    Invoke-RestMethod -Method Post -Uri "http://$Mac/hello" -Body $body -ContentType 'application/json; charset=utf-8' | Out-Null
    Ok "Mac got: $($env:COMPUTERNAME), $($report.ips -join ', '), user $($env:USERNAME)"
} catch { Warn "The Mac did not answer ($Mac). Tell Claude: $($env:COMPUTERNAME), $($report.ips -join ', '), $($env:USERNAME)" }

Write-Host "`nDone. Back to the Mac." -ForegroundColor Green
}
