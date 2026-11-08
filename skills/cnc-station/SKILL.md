---
name: cnc-station
description: Set up and run a CNC operator station - a Windows laptop with gSender on the machine's USB, fed from the Mac with posted G-code and a page of job steps. Use when connecting a GRBL CNC machine, bootstrapping the laptop next to it, writing or syncing a job's steps and files, reading GRBL settings, or when the operator page or sync misbehaves.
---

# CNC operator station

The Mac does the CAM (`fusion-cam` skill). The laptop next to the machine keeps the G-code stream on USB, so a Wi-Fi drop never stalls a cut with the spindle running. Wi-Fi only carries files, SSH and the sender's remote UI.

**Safety:** Start, jog, homing and the spindle are pressed by a person at the machine. Over SSH you read state and load files, nothing else.

## Set up

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/setup_project.py <dir> --cnc "<machine> · gSender" --lang en
```

This creates `cnc/` with the following files. It never overwrites `station.json` or `jobs/`, and `--update-tools` refreshes the scripts.

| Path | Purpose |
|---|---|
| `station.json` | machine name on the page, page language (`en`, `uk`), laptop folder |
| `jobs/<NN-name>/job.json` | one job: steps, facts, files, picture. Starts with `00-connect` |
| `station/CNC.html`, `station/build.py` | the operator page and its data builder |
| `sync.sh`, `ssh.sh`, `grbl.sh` | push to the laptop, run a command there, read GRBL settings |
| `laptop/serve.py`, `laptop/setup.ps1` | one-time laptop bootstrap |
| `nc_check.py` | G-code check, also run by `build.py` |

**Laptop bootstrap**, once:

1. Make the key: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_cnc -N ''`.
2. Run `/usr/bin/python3 cnc/laptop/serve.py` in the background. Use Apple's python: the macOS firewall lets it accept LAN connections without a prompt.
3. Give the user the line for an admin PowerShell on the laptop: `irm http://<mac-ip>:8770/setup.ps1 | iex`.

The script:

- sets the network to Private;
- turns sleep and USB suspend off on the charger;
- stops automatic restarts from Windows Update;
- installs OpenSSH with the key;
- creates `C:\cnc` with a desktop shortcut;
- installs gSender;
- reports back into `cnc/laptop/laptop.json`.

Stop `serve.py` afterwards.

## Jobs

```json
{"title": "1 · Holes", "status": "ready", "summary": "...", "image": "setup.svg",
 "facts": [["Stock", "..."], ["Tool", "..."], ["Zero XYZ", "top back-left corner"]],
 "files": [{"name": "holes.nc", "tool": "T3 · 3.175 flat"}],
 "steps": [{"text": "**Raise Z** ...", "danger": true, "note": "why"}]}
```

- **Status:** `ready`, `draft`, `later` or `done`. Text takes `**bold**` and `` `code` ``.
- **Files:** `build.py` runs `nc_check` on every file. The page shows the depth, top height and time read from the file. A file with an error stops the build. Never write depths or times by hand in `facts`.
- **Picture:** an SVG with the stock, the zero point (big, labelled) and where the clamps may go, away from the toolpath zone.
- **Steps cover, in order:**
  - clamps away from the toolpath and from the zero corner;
  - Unlock and Home after an alarm;
  - X0 and Y0, then Z0;
  - **raise Z** before Start, because a `clearanceHeight` program starts sideways at the current Z;
  - Load File with the exact path (the page has a copy button);
  - Start;
  - stay at the emergency stop.
- **Order:** a job that changes the stock (bolting down, flipping, a tool change) is a new job, with zero set again.

After every change in `cnc/jobs`, run `cnc/sync.sh` without asking. It rebuilds the page and mirrors it to `C:\cnc`, and jobs removed here disappear there. The open page reloads its data every 4 s, and ticked steps reset when a job's files change.

## Reading the machine

- `cnc/grbl.sh` sends `$I $$ $# $G ?` (read-only queries only; anything else is refused).
- It works only while no sender holds the port. A port is exclusive, so an open sender gives "Access denied", and a second sender (Candle, UGS) also blocks gSender.
- See `fusion-cam`, "Machine settings", for what to look at.

## Pitfalls

- **Limit switch hit:** the position is lost. The operator presses Unlock and Home, then sets the zero again.
- **Two addresses on the laptop** (a Hyper-V or VPN adapter): gSender's remote mode must bind the LAN one. `serve.py` picks the address on the Mac's subnet.
- **gSender connects but stays in Alarm:** homing is on (`$22=1`), so Home first.
- **No COM port:** a CH340 controller needs its driver on older Windows; FTDI and CP210x usually do not.
