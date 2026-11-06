#!/usr/bin/env python3
"""Close Bambu Studio, run a command that edits a project, reopen that project, only if Bambu was open before.
    python3 bambulab/tools/with_bambu_closed.py <project.3mf>[,<project2.3mf>…] <command…>
Bambu quits normally: with unsaved changes it shows its own save prompt, which the user answers. If it is still open after
two minutes, nothing is edited and the exit code is 1. Never fall back to writing the file directly: the open Bambu would keep
a stale copy and its next save would overwrite the edit. Tested on macOS; the Windows and Linux paths are best effort."""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import bambu3mf

def uptime():
    if sys.platform == 'win32': return 60
    pid = subprocess.run(['pgrep', '-x', 'BambuStudio' if sys.platform == 'darwin' else 'bambu-studio'], capture_output=True, text=True).stdout.split()[0]
    parts = subprocess.run(['ps', '-o', 'etime=', '-p', pid], capture_output=True, text=True).stdout.strip().replace('-', ':').split(':')
    s = 0
    for p in parts: s = s * 60 + int(p)
    return s

def ask_to_quit():
    if sys.platform == 'darwin': subprocess.run(['osascript', '-e', 'tell application "BambuStudio" to quit'], capture_output=True)
    elif sys.platform == 'win32': subprocess.run(['taskkill', '/IM', 'bambu-studio.exe'], capture_output=True)   # no /F: a close request
    else: subprocess.run(['pkill', '-TERM', '-x', 'bambu-studio'], capture_output=True)

def reopen(proj):
    if sys.platform == 'darwin': subprocess.run(['open', '-a', 'BambuStudio', proj])
    else: subprocess.Popen([bambu3mf.studio_path(), proj])

def main():
    if len(sys.argv) < 3: sys.exit(__doc__)
    projects = [os.path.abspath(p) for p in sys.argv[1].split(',')]
    for p in projects:
        if not os.path.isfile(p): sys.exit('no project: ' + p)
    was_open = bambu3mf.studio_running()
    if was_open:
        time.sleep(max(0, 30 - uptime()))        # quitting during startup can crash it
        ask_to_quit()
        for _ in range(240):
            if not bambu3mf.studio_running(): break
            time.sleep(0.5)
        else: sys.exit('Bambu Studio is still open (a save prompt?): nothing edited. Answer it, then run this again.')
    code = subprocess.run(sys.argv[2:]).returncode
    if was_open: reopen(projects[0])             # one only: Bambu holds one project, a second open imports its parts as geometry
    sys.exit(code)

if __name__ == '__main__': main()
