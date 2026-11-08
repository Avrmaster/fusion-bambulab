#!/usr/bin/env python3
"""Close Bambu Studio, run a command that edits a project, reopen that project, only if Bambu was open before.
    python3 bambulab/tools/with_bambu_closed.py [--reopen] <project.3mf>[,<project2.3mf>…] <command…>
An open Bambu is not a problem: this asks it to quit. With nothing unsaved it closes in a few seconds, the command runs and
the project is reopened. With unsaved changes Bambu shows its own save prompt instead: then this exits at once with code 3
and edits nothing. Ask the user whether to keep those changes; they answer Bambu's prompt (Save / Don't Save), and you run
again with --reopen, which waits up to 10 minutes for Bambu to close and reopens the project afterwards even though Bambu
was closed when it started. Never fall back to writing the file directly: the open Bambu would keep a stale copy and its
next save would overwrite the edit. Tested on macOS; the Windows and Linux paths are best effort."""
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

def wait_closed(seconds):
    for _ in range(int(seconds * 2)):
        if not bambu3mf.studio_running(): return True
        time.sleep(0.5)
    return not bambu3mf.studio_running()

def main():
    args = sys.argv[1:]
    reopen_after = '--reopen' in args
    if reopen_after: args.remove('--reopen')
    if len(args) < 2: sys.exit(__doc__)
    projects = [os.path.abspath(p) for p in args[0].split(',')]
    for p in projects:
        if not os.path.isfile(p): sys.exit('no project: ' + p)
    was_open = bambu3mf.studio_running()
    if was_open:
        time.sleep(max(0, 30 - uptime()))        # quitting during startup can crash it
        ask_to_quit()
        if not wait_closed(600 if reopen_after else 10):
            print('Bambu Studio has unsaved changes and shows its save prompt: nothing edited. Ask the user whether to save '
                  'them, let them answer the prompt in Bambu, then run again with --reopen.', file=sys.stderr)
            sys.exit(3)
    code = subprocess.run(args[1:]).returncode
    if was_open or reopen_after: reopen(projects[0])   # one only: Bambu holds one project, a second open imports its parts as geometry
    sys.exit(code)

if __name__ == '__main__': main()
