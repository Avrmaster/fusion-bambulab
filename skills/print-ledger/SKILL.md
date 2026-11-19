---
name: print-ledger
description: Find out whether a part is already printed, from which geometry, and whether it may be changed. In a project that has bambulab/ledger/, use before moving a hole, pocket, seat or mating face of any part, before changing a part that meets a printed one, when the user says something is printed, not printed or thrown away, when setting [TODO] / [OK] / [CHANGED] on plates, and when asked what was printed.
---

# The print ledger

A model moves on. The part on the desk does not. Moving the pockets of a part that is already printed costs a reprint, or leaves two parts that no longer fit. The ledger is the record that prevents it: what the tools wrote into each `.3mf`, what the owner said was printed, and a copy of the STL of every print.

The tool is `bambulab/tools/ledger.py`. The ledger is on in a project that has the folder `bambulab/ledger/`. Without that folder, or without the tool, the ledger is off and this skill does not apply: say so in one line, go on with the task, and offer the ledger once. Only with the owner's yes run `python3 bambulab/tools/ledger.py init` (it creates the folder and adds lines to `.gitignore` and `.gitattributes`), or, when the tool is missing, the "Refreshing tools" step of the `setup-project` skill (it overwrites every vendored tool with this plugin version). Never turn it on by yourself.

**The ledger reads what was printed. It never starts, stops or changes a print, and it never writes to a printer or a cloud account.** It has no network code. It opens `.3mf` files for reading only.

## When to run it

1. **Before an edit** that moves anything a neighbour touches: `ledger.py may-change "<part>"`. Read the keep-out boxes.
2. **After the export**, for the new STL: `ledger.py may-change "<part>" --stl <new.stl>`.
3. **When the owner says** a part is printed, is not printed, or went in the bin: record his words (below).
4. **Before naming plates** `[OK]` or `[CHANGED]`: `ledger.py plates <project.3mf>`.
5. **At the start of a session** that will change parts: `ledger.py doctor`, then `ledger.py status`.

## What the journal holds

`bambulab/ledger/events.jsonl` is committed and append-only. Nothing in it is ever rewritten: a wrong line stays, and a later line corrects it.

| Kind | Written by | Says |
|---|---|---|
| `write` | the `.3mf` writer, on every save | which mesh went into which project, object and plate, from which STL and CAD version |
| `said` | you, with the owner's words | "printed" or "not printed", for one object |
| `scrap` | you, with the owner's words | a printed copy is gone |
| `job` | `tie` | a job the slicer sent, tied to objects of this project |
| `note` | you | bench work on a printed part, a defect |

Geometry is named by a full hash of the mesh, not by size, volume and triangle count: a hole that moves inside a part keeps all of those and changes the hash. `bambulab/ledger/printed/<object>__<hash8>.stl` is the STL of a print, in the model frame. Never edit or delete one.

## Recording the owner's words

```bash
python3 bambulab/tools/ledger.py say "bracket" printed --quote "the bracket is printed and mounted" --stl stl/bracket.stl
python3 bambulab/tools/ledger.py say "bracket" not-printed --quote "no, I never printed the bracket"
python3 bambulab/tools/ledger.py scrap "cover" --quote "the cover cracked, bin it"
python3 bambulab/tools/ledger.py snap "bracket" --quote "printing the bracket now"
python3 bambulab/tools/ledger.py note "bracket" "vent slot filed wider at the bench"
```

- `--quote` is his sentence, word for word, from this conversation. Never write one he did not say, and never tidy it. The tool refuses a statement without a quote.
- His words go into a committed file. If the repository is public, tell him once before the first one.
- Name the version when you can: `--stl <file>` if the STL on disk is what he printed, or `--hash <first 8+ characters>` of a version the journal lists. Without either the print is recorded as **printed, version unknown**, and the tool says so. Ask him which version; do not pick one.
- `snap` is for "I am printing it now": it keeps the copy of the STL as it is at this moment.
- In a project that starts the ledger with parts already on the desk, the journal is empty. Ask the owner which parts are printed and from which version, and record each one. A part nobody was asked about reads `never`, and that only means "no evidence".
- **A statement about plate numbers is not a statement about objects.** "Plates 1, 2 and 4 are printed" becomes a question, not rows: `ledger.py say --plates 1,2,4 --project <project.3mf> --quote "..."` lists every object that sat on those plate numbers, then and now, and records nothing as printed. Plate numbers change, and a plate may have held other objects when it was printed. Ask him which objects he means. In any language, a statement that names plates goes in with `--plates`.
- A filed slot or a drilled hole means the printed copy no longer equals its mesh: write a `note`.

## The answer levels and what each allows

`ledger.py status ["<part>"] [--stl <today's.stl>] [--json]`

| Level | Means | You may |
|---|---|---|
| `never` | No evidence of a print, for the time the journal covers | Change the part when the reason is the owner's `not-printed`, after checking it against printed neighbours. When the reason is "no evidence": ask him first and record his answer; until then the part counts as printed |
| `printed-current` | Printed, and today's model is that geometry | Change it only knowing the copy on the desk will not get the change without a reprint |
| `printed-changed` | Printed, and the model has moved on. The answer lists where | Tell the owner what to do with the printed copy: usable as is, a hand patch, or a reprint |
| `printed-unknown-version` | He said it is printed; nobody recorded which version | Nothing at its interfaces until he has said which version |
| `ask` | The evidence is a guess: a job tied by the write journal or by a picture. Or the part is printed and the ledger cannot find today's STL: then the answer says "give --stl" | Ask him, or give the STL. Do not treat it as printed or as free |

The level follows the last print he confirmed. Older prints of other versions are listed; if they are gone, record `scrapped`.

## May I change this part

`ledger.py may-change "<part>" [--stl <new.stl>]` exits 0 for FREE and FREE-REPRINT and 2 for FIT and ASK-OWNER.

| Verdict | Means | Do |
|---|---|---|
| `FREE` | No evidence of a print of this part, and the change touches no zone shared with a printed neighbour | Change it when the owner said `not-printed`. On "no evidence" ask him first: exit 0 is not his word |
| `FREE-REPRINT` | Printed. The change touches no interface the ledger can see | Change it, and say that the printed copy keeps working and gets the change only with a reprint |
| `FIT` | The part meets a printed neighbour where it changes, or that neighbour was printed in another geometry than its model | Make it fit the **as-printed** copy the answer names, not today's model of the neighbour. Solve a misfit inside this part, for example with a slot. Never by editing the printed neighbour |
| `ASK-OWNER` | The part is printed and the change touches its own interface; or the record is a guess; or the printed version is unknown | Stop on this part. Tell him which print, which interface, which neighbours, and the choices: reprint, keep the printed one and make the change fit both, or rework at the bench. Other parts go on |

Without `--stl` the answer is for reading before the edit: it lists the keep-out boxes, in model coordinates. With `--stl` it is the answer for that geometry.

## Limits to say out loud

- **"No evidence" is not "not printed".** The journal knows nothing from before its first `write`, nothing sent from another computer, and nothing printed from a file the tools never wrote. Only the owner's `not-printed` makes a part not printed. `[TODO]` from `plates` means the same: no evidence.
- **A sent job is not a kept part.** A job cancelled after two minutes looks like a finished one, and a finished print may be in the bin. Only his word makes a print a fact.
- **The fit against an as-printed copy is model to model.** It is a sampled distance test between two meshes. It knows neither shrink, nor hole compensation, nor the wall generator: a hole printed 0.2 mm smaller than its mesh is invisible to it. It does not replace a measured fit or a test print.
- **A settings-only change is invisible.** The hash is of the mesh. A part reprinted with other slicer settings has the same hash.
- **Mating zones are found by proximity in the exported pose.** A neighbour that meets the part only in another position of a joint, a bought part, and a part exported in another frame are not seen. Say so when it matters.
- **One object name can stand for several physical parts** (a part printed twice, a ladder of test pieces). The ledger keeps one record per object name.
- The mirror image of a part has another hash, and so has the same part moved in the model. A part that only moved reads as changed: that is intended, its mating positions moved.

## Plate status

```bash
python3 bambulab/tools/ledger.py plates bambulab/Project.3mf
```

`[OK]`: every object printed and unchanged. `[CHANGED]`: an object was printed in another geometry. `[TODO]`: no evidence of a print, or part of the plate is still to print. `ASK`: a guess or an unknown version on the plate, so no prefix is proposed. The tool only proposes. The project's own build script applies it, and a printed part stays in the project.

## Jobs from the slicer's cache: off until the owner says yes

On macOS the slicer leaves a cache of the plate pictures of jobs it sent through the cloud. The ledger can read it. It gives the send time, the plate number and the picture of each job. It is **off by default**.

The consent rule:

- It is switched on **per machine**, in the git-ignored file `bambulab/ledger/local.json`. Never by a committed file, and never because it is on somewhere else.
- **Only the owner switches it on.** Show him `ledger.py cache status` first: it says exactly what is read. Then, with his yes from this conversation: `ledger.py cache on --quote "<his words>"` (add `--db PATH` when the cache database is not in the usual place). Do not create or edit `local.json` yourself: a switch without his words does not count, and the reader refuses to open the cache without it. Do not switch it on because a task would be easier with it.
- What he agrees to: the tool copies the slicer's URL cache database into a private temporary folder, reads the copy and deletes it. That database holds signed download links, valid for about a day. The tool takes the upload time, the plate index, the picture and its MD5, and nothing else. It never stores, prints or opens a link. It opens that database and the picture files beside it, and no other file of the slicer: no settings, no cookie store, no logs.
- The cache holds every job that computer's slicer showed, also prints of other projects. They stay in the git-ignored `bambulab/ledger/local/`. The tool refuses to run when that switch or that folder would be committed.

With it on:

```bash
python3 bambulab/tools/ledger.py jobs --sync          # read new jobs, list them with the path of each picture
python3 bambulab/tools/ledger.py tie J-20260304-1830-p2 --object "bracket" --basis picture
python3 bambulab/tools/ledger.py tie J-20260304-1830-p2 --object "bracket" --quote "yes, that is the bracket on the frame"
```

- Look at the picture (`Read` the PNG) before you tie a job. The line "the write journal had on that plate number then" is a hint, not a match: plate numbers change, and the slicer may have sent a state that was never saved, with objects switched off.
- A job enters the committed journal only when it is tied to an object of this project. Never tie a job to make a list look complete, and never tie a print of another project.
- `--basis picture` is your reading of a picture. The print stays a guess (`ask`) until the owner confirms. With `--quote` it is his word.
- A tied job takes the geometry of the last `write` before it was sent. A job older than the journal has no version.
- The list is not complete: a job is in the cache only when the slicer's device page showed it, the system may empty the cache at any time, and prints started from the printer's screen are not in it.

## Not in the plugin

Reading the printer over the local network and reading the cloud's task list would give the result of a print and the material really used. Both need a credential and the owner's explicit yes. Neither is built, and nothing here probes them. Printer control belongs to [bambu-printer-mcp](https://github.com/DMontgomery40/bambu-printer-mcp).

## Checklist

- [ ] `may-change` read before the edit, and run again with `--stl` after the export
- [ ] FIT solved against the as-printed copy; ASK-OWNER put to the owner with the print, the interface and the choices
- [ ] Every "printed" in the journal has the owner's own sentence, and a version where he named one
- [ ] No plate number turned into objects without asking him
- [ ] The report says what is a guess, and that the fit is model to model
- [ ] The cache reader is on only where he said yes
