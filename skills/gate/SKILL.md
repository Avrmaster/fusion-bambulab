---
name: gate
description: Work with the print gate of a project, the registry of design rules, checks and stamps that stands between a Fusion change and a printed part. Use in any project that has gate/project.json, before changing a printed part, after changing it, before exporting an STL or writing a .3mf, when gate.py reports findings or questions, when a rule or a number is in doubt, and when adding the gate to a project.
---

# The print gate

A rule that lives only in prose gets forgotten, and the print pays for it. The gate holds the rules in a registry, finds the parts by itself, runs the checks, and writes a **stamp** for each geometry that passed.

**A part is checked only when its stamp exists.** A report, a printed table, a "TOTAL 0" from another script, or your own reading of the model is not a pass. Say "checked" only for a part whose stamp was written for its present geometry.

The gate is in a project when `gate/project.json` exists. Its one command is `python3 gate/tools/gate.py`, run from the project root. Without that file this skill does not apply; `python3 ${CLAUDE_PLUGIN_ROOT}/tools/setup_project.py <dir> --gate` adds it (see "Adding the gate" below).

## Exit codes

Every sub-command ends with one of these. Read the code, not only the text.

| Code | Meaning | What you do |
|---|---|---|
| 0 | pass | go on |
| 1 | open findings that block, or checks that could not decide | fix what failed, in the fix order of each rule. When the run says "nothing failed", nothing in the model is known to be wrong: do what each `NOT_RUN` row says, then run again |
| 2 | questions are open | answer them honestly (`attest list`) |
| 3 | more calls needed; the last line is `NEXT n/m` | run the same command again, until it is not 3 |
| 4 | the gate could not run: no project, a broken file, a parameter error, Fusion not answering, a forked copy, several declared targets and none named | repair the cause; `gate.py doctor` names it. Never treat 4 as "nothing found" |

A check that did not run is listed as `NOT_RUN`. It counts as open, never as passed. A part whose open rows are all `NOT_RUN` has the verdict `incomplete`, not `fail`.

The first line of a run gives the counts by verdict: `3 parts, 14 open (2 FAIL, 6 BLOCKED, 5 GROW, 1 NOT_RUN), 4 questions (2 answered by facts), 1 not run -> exit 1`. Under it, equal rows of a part are one line (`x6`, the first id, `+5 more`), what is measured and wrong first. Read those lines; the report holds every row.

## Before a change

1. `python3 gate/tools/gate.py status`. It needs no Fusion. It shows open findings, open questions, parts changed since their stamp and STLs without a valid stamp. It exits 1 while a finding is open, a changed part was not checked, or an STL in a registered folder has no valid stamp. The same digest is printed at session start.
2. Read the rules of the area you will touch, with this project's numbers: `gate/RULES.md`, or `python3 gate/tools/gate.py rules --category inserts`. The `print-design` skill carries the same rules with the default numbers.
3. Before a hole, a pocket, a seat or a mating face moves: read the print ledger for the part and its mates, `python3 bambulab/tools/ledger.py status "<object>"` and `may-change "<object>"`. The ledger knows a part by the name of its object in the .3mf (`Bracket`), not by its gate part id (`demo/bracket`). A part with no record counts as printed: a `never` or a `FREE` whose reason is "no evidence" is no record, and exit 0 there is not a permission. Only the owner's recorded `not-printed` frees the part: ask him and record his words before the interface moves.
4. Say which parts you intend to change. You will pass them to the run.

## After a change

```bash
python3 gate/tools/gate.py run --changed --parts demo/bracket,demo/cover
```

- `--parts` is the intent: the parts you meant to change. A part whose geometry changed outside that list is a finding. A hole or pocket that vanished from a part is a finding too.
- The run talks to Fusion in short calls and stops at its wall time. Exit 3 is normal: call it again. Do not replace it with a long script of your own.
- A document that is not open is `unreachable`: its parts keep their old stamps and get no new one. Open the document; do not skip the part.
- A run starts with a scan. A finished scan prints notes about the whole document, once: `LINKED` (printed bodies that come from a linked document, and who judges them), `HINT` (parts that are probably bought: a few faces, or many equal bodies in one component) and `NO GROUP` (parts in no motion group). A hint excludes nothing: the owner adds a `not_printed` pattern or leaves the part. While a scan needs more calls it prints `scan: not finished` and no counts.
- A large body can take more than one call before a unit measures anything: the unit answers that it is still signing the body, and the same command goes on. That is exit 3, not an error.
- A unit that answers after the gate stopped waiting is not lost. The call that timed out exits 4. Run the same command again: while Fusion is still busy with that unit it exits 4 again and sends nothing; when the unit has written its result, the run reads it and goes on. A `scan` or `export` in between changes nothing about that. Never delete a file under `gate/run/` to "unblock" a run, and never send the unit by hand. When the exit 4 says that nothing listens at the endpoint, Fusion is not running: start it and run again.
- `OVER BUDGET` lines (in the output of `run` and `scan`, in `status`, `doctor` and the report) name each call that worked longer than its unit's budget, each call that got no answer and left no result, and each unit that was given up after two such calls in a row (its rules are `NOT_RUN`, the run goes on and exits 1). Such a line is a defect of a probe, not a finding of the part: tell the owner, with the line, so it can be reported to the plugin. A `UNIT ERROR` line is the error text of a probe that ended a unit.
- An audit (`run --audit`) blocks nothing, with one exception: a part one of whose units was given up over its budget is not inherited. Nothing judged the rules of that unit, so the audit exits 1 and names the part; run it again (`run --part ID`) when the probe is fixed or the model is lighter.
- A note "the bodies round the part changed between two calls of this unit: ... is measured again from its start" means that a neighbour was edited while a unit was between two calls. That is right once. When a unit ends in the error "met other bodies than the call before in three calls in a row" and nobody edited the model, report it: a value read from Fusion is not stable.
- Facts are stored per geometry and judged again by today's judges. When a row says that the facts come from an older unit and the part is to be run again, do that (`run --part ID`): the judge does not guess from old facts.
- `python3 gate/tools/gate.py report --last` prints the last report again. Rows of one part with the same rule, cause and neighbour are one block with the count and the places; the fix order of a rule is printed once. `gate/reports/<run>.json` holds every finding with its own id: a waiver is per feature.
- A project with several .3mf projects declares several targets. A part is judged for one of them (its layer height, the fits measured under it), so name it: `run --changed --for <key>`. Without `--for` such a run exits 4 and judges nothing. `export --for <key>` judges the stored facts again for its own target, so one run serves the export for every project.

The verdict of a part is summed by the tool from every stored check of its present geometry. Do not read a verdict off one call's output.

## Fixing a finding

Each finding has a rule, a part, a place, a measured value and the limit with its tag, and one of these verdicts:

| Verdict | Meaning |
|---|---|
| `FAIL` | under the hard floor of the rule, or plainly wrong |
| `GROW` | under the target, and there is free space: fix it |
| `BLOCKED` | under the target, and a named neighbour stops the fix: this goes to the owner |
| `NOT_RUN` | the check could not decide: run it, do not argue it away |
| `INFO` | told, never blocking |

**Fix in the order the rule registers.** The finding prints that order, `gate/RULES.md` holds it. Take step 1 unless you can say what stops it, then step 2, and so on. For a thin wall round a heat-set pocket the order is: make the whole wall or shoulder wider where the outline is free; move the pocket with its screw; a local boss only where the outline cannot move; blocked on every side, report it with what stops it. Do not jump to the smallest patch.

After the fix run the gate again. A fix moves other things: a relief cut moves the lead-in, a thicker wall can close a path.

Some rows are about what the gate could see, not about the part:

- **Hardware the gate did not recognise** (`hardware.recognised`, `NOT_RUN`). A name picks the candidates and the shape decides: a screw is a head on a shank of a table thread (one body, or a head body and a shank body in one component), a bearing is a ring of a table row, a washer a thin ring. A body that a name pattern picked and whose shape says otherwise is listed and not judged (a magnet in a component of screws; a clamp screw named after the bearing it clamps). A screw hole with a bought body on its axis that nothing recognised means that no screw rule looked at that hole: name the ref as what it is (thread and length, `M3x12`, or the word; `pin`, `dowel`), or ask the owner to set the ref pattern of the project (`params explain screws.ref_pattern`; the patterns for washers, inserts, nuts, bearings and motors are parameters too). Naming a ref so that it IS found is the fix; never rename one so that it is not.
- **Other parts in front of openings** (`access.hole_covered`, `access.screw_path`): ONE row and one question per part and rule, for every part that stands in front of its openings or of its screw heads: nearest first, each with its distance and what it covers. It asks the assembly order: free only if each of them is mounted later and comes off first. The row keeps its id while the same parts stand in front of the same openings, however far out; it is asked again, with the whole list, when a covering part joins or leaves, or comes to stand in front of another opening. A body inside a bought or linked assembly is named with that assembly (`Gearbox / Motor / Rotor`). The part itself in the path is a `FAIL` per feature. What sits in an opening by design is no cover: the screw in its hole, the bearing in its seat, the bought part or fastener that reaches into a slot or window. A mesh body or a printed neighbour inside a slot or window says nothing about what it is for: it is judged like any cover. A seat or screw hole of the census that has no opening row at an outer end was not measured: `NOT_RUN`, run the part again.
- **A mesh body in a path** is never a `FAIL`: a mesh has no exact shape. The row stays `NOT_RUN` until the mesh is a solid body. When the mesh is no part of the build (an old version laid over the model), only the owner can say so: he sets `access.overlay_pattern`.
- **A one-layer skin between a counterbore and its hole** is asked once (`access.bridge_layer`): a bridge layer that is opened at the bench, or a leftover skin. Answer from the model, and put the bench step into the build notes.
- **Insert pockets**: one pocket gives one row per cause. The row of `inserts.wall` names the wall beside the melt relief too; `inserts.wall_at_relief` has the row when the wall round the lead-in passes, or when a smaller relief of the table would fit. A relief wall that is thin for a reason of its own (thinner than the lead-in wall and the step between the two diameters explain: 0.2 mm towards the relief of the next pocket) is a second row, in `inserts.wall_at_relief`: the worst wall of a pocket is never only a text. A pocket that ends one relief depth short of its table row (its relief was cut later and the hole was not deepened) is a row of `inserts.pocket_shape` that names this cause; the owner decides the group once. A hole that holds an insert or a screw is never taken for a magnet pocket, whatever its diameter.
- **A ring that was not judged** (`bearings.holding`, `NOT_RUN`): a bearing whose model is off its table row (a 608 drawn 7.3 wide), a ring the bearing unit passed over, a body named like a bearing whose shape could not be read. Correct the model of the bought part or the table; a pin or a clamp screw named after a bearing is only told (`INFO`).
- **Bearings inside a bought assembly** (`bearings.holding`, `INFO`): a bearing that bought bodies hold and at whose rings no printed part stands, seats or clamps (the rollers inside a gearbox) is not the finding of a printed part beside it. One row tells how many and which, and names the mesh bodies near them (a part drawn as a mesh cannot be measured). A ring that a printed part holds, or stops short of, is judged for that part; a bearing that nothing touches at all stays a `FAIL`.
- **A washer over a slot** passes when it reaches at least `washers.slot_land_min` over each flank and its ring lies on material everywhere beside the slot. Over a slot nearly as wide as itself it fails.
- **Narrow faces** (`walls.thin_faces`): a flat of a chamfer or of a tooth flank and the bridge steps under a counterbore are told in one `INFO` row, not failed. Narrow faces that touch are one row for the place. Equal places that repeat round one axis (the teeth of a wheel, a spline, a knurl: twelve or more on one even division of a circle) are one row for the profile: one decision covers it, and a place that differs keeps its own row.
- **A gap along a screw** (`screws.shell_bears`): a gap that a bought body bridges (a bearing ring the two parts clamp between them: on one line along the screw both parts press on it) is the clamp room of that body: one `INFO` row names it. A gap with nothing in it is a `FAIL` per screw. A body in the gap that only one part presses on (a ring in a bore with no shoulder behind it, a pin through both parts, a part that stops short of the body) closes nothing: one `FAIL` row for all the screws of the part across that gap, with what each part does to the body. Facts of an older screw unit do not say what stands in a gap: the `FAIL` stays and says to collect the facts again.
- **A thread that nothing receives** (`screws.engagement`, `FAIL` with 0 mm): the row names the hole, the part the thread ends in and what lies on that axis. Model the insert or the nut as a ref body, or name the body that is there so that its pattern matches. A head that bears on a bearing ring through its washer is not judged for plastic under it (`screws.under_head`).
- **A hole that only changed its class** (`regression.feature_lost`, `INFO`): the same hole with the same segments, called an insert pocket or a plain hole by another version of the tables or tools, is not lost. A hole whose segments changed, or that is gone, is asked as before.

Never close a finding by any of these:

- editing a number, a waiver, the triage list or the mode. Those are the owner's.
- renaming a body so that it is no longer found, or removing a hardware ref.
- taking a document, an STL folder or a .3mf out of `gate/layout.json`.
- changing a file under `gate/tools/` or `gate/rules/`.
- exporting the STL another way.

## Questions

Some rules cannot be checked by a script. The gate asks them, per changed part or per finding. A question about the whole part is asked only where it can apply: when the facts of the part prove that it cannot, the gate gives the answer itself and the report lists it under "Answered by facts". Such an answer is not yours and is not counted as attested. The proof is strict. Every unit the test reads must have facts for this geometry. A name can only make a question asked, never answered: a component named after its bearings, a bought body near under a name no pattern knows (the "Rotor" of a motor model) and a body that a hardware name picked and the shape refused all leave the question open. "Nothing recognised" is no proof: while the part has a screw hole, a slot, a hole a screw could use (up to `screws.any_hole_d_max`) or a bought body on an axis, the question is asked. What counts as near is `process.near_margin`; what counts as a motor or a connector is the name pattern `motors.ref_pattern`. `attest list` prints, per rule, what it asks and the answers it takes once, then one line per question; a line "the facts: ..." says why a question is asked here.

```bash
python3 gate/tools/gate.py attest list
python3 gate/tools/gate.py attest answer Q-51b0c2 --label "bed face is the modelled bottom; nothing faces down unsupported"
python3 gate/tools/gate.py attest answer Q-7c02aa --shot .shots/bracket_pocket.png
python3 gate/tools/gate.py attest answer Q-19ab04 --na "this part has no screws at all"
python3 gate/tools/gate.py attest answer Q-33c1d0 --param fits.sliding_per_side
```

Answer what is true, after looking:

- A **label** is one of the fixed answers, word for word. Pick the one that is true, also when it is the one that says "listed in the report for the owner". If none is true, do not answer: tell the owner. A label that the facts of the part contradict is refused: "this part has no screws" while the screw probe lists two, a washer, a screw that passes the part, or a screw hole with a bought body on its axis that nothing recognised (then read the row of `hardware.recognised` first). A "not applicable" that says the same thing is refused the same way. What only could be there refuses nothing: an empty hole a screw could use makes the question asked, and the answer is your word.
- An answer about the whole part holds while the facts do not contradict it. When a unit is run again for the same geometry and now shows what the answer denied, the question is open again (`attest list` says why), and a stamp that rested on that answer is stale.
- A **shot** is a screenshot of the changed zone, taken after the change and looked at. An old picture is refused.
- A **param** points at a number of this project with tag M (measured on a print) or U (owner decision). A number that is only decided (D) is not evidence, and a value of the declared target never is. For "what goes in this pocket" the parameter must be a fit and its value must be the diameter of that pocket: a pocket cut from a typed number is not covered by naming some parameter. With several declared targets add `--for <key>`.
- **na** needs the reason, in words that say something.
- You never type a free number, and you never answer for the owner. A question that only a waiver closes waits for him.

The export questions (`access.hole_supports`, `print.bed_face`) are asked when the new geometry is in no .3mf project yet. Answer from the model. Take a label that speaks of the sliced plate only when this geometry was sliced. Otherwise take the label that says the look at the slice is owed: the report lists it for the owner, and you do that look after the part is placed, before the print.

An answer is tied to the feature and its state. When the feature changes, the question comes back. A question about the whole part is asked again for every new geometry of that part: what is true of its screws and its surroundings changes with it. When a waiver behind an answer is withdrawn, the stamp that rested on it is open again. A stamp that rests on answers is `pass_attested`, and the report lists those answers for the owner: they are your statements, and he reads them.

## Before an export

```bash
python3 gate/tools/gate.py export demo/bracket
python3 gate/tools/gate.py export --generated stl/shim_05.stl
```

Export through the gate, never around it. It needs the design verdict to pass, writes the STL into quarantine, measures the mesh and its walls there, asks the export questions, and only then moves the file into the STL folder and writes the stamp. A part that fails stays in quarantine and the STL folder keeps the previous file. Its wall or mesh finding stays open: the next `run` shows it and exits 1 until the body is changed.

A part made by code (`--generated`) must be registered under `generators` in `gate/layout.json`. Use `--for <key>` when the project has several .3mf projects: a stamp is valid for the target it was judged for, and a part that goes into two projects is exported once for each.

Several parts can be named in one call. One export unit is one Fusion call with the unit budget of `gate/layout.json` (`"fusion": {"budget_s": 20}`); the parts that are left wait, the call exits 3, and the same command again goes on with them (`--wall` is the time of the whole command). A body with thousands of faces can use up that budget for its signature alone: then nothing is meshed, the answer says so, and that part needs a longer `budget_s`. Every export call writes a report: `report --last` shows the answers the new stamps rest on, the export questions included.

The stamp dies with the geometry. Any later change of the body needs a new run and a new export.

## Writing a .3mf

The .3mf writer (`bambu3mf.save`) asks the gate about every mesh it is about to write.

This costs time at every save, also a settings-only one and also in `observe` mode: the meshes of the archive and of the file on disk are parsed and hashed in pure Python. Count on about 5 to 15 s per million triangles per save (measured: 0.77 million triangles, 0.7 s without the gate, 4 to 12 s with it on Python 3.9); round parts placed in the same run can take several times longer. Nothing fails; a tool that saves more than once pays it each time.

- In `observe` mode it reports unstamped and stale meshes in one line and in `gate/reports/guard.log`, and writes the file.
- In `enforce` mode it refuses a new mesh without a valid stamp, and writes nothing.
- In both modes it compares the fit settings of the save (hole compensation, layer height, first layer height and the rest of `target.fit_settings`) with the file on disk, global and per object. A change the caller did not name is refused in `enforce` mode and is one loud line in `observe` mode. A change that was named is printed in one line, never silent.
- A change is named by the tool that makes it (`set_project`, `set_settings`, `build`) or with `bambu3mf.note_setting`. A note lasts for one save.
- A key that leaves the project's list of settings that differ from the system preset while its value stays is refused too: Bambu Studio takes the preset's value for a key that is not in that list. Set global values with `bambu3mf.set_project`: it lists the key.
- The guard remembers the fit settings of each project file in `gate/fit_settings.json`. A project that was deleted and is built again from a template is compared with what it held before. Commit that file with the project. When a file's settings changed since the last guarded save and no tool wrote them (Bambu Studio saved the project, or a file was copied over it), the next save says so in one line; `verify` is the check for it.
- In `enforce` mode a save is refused when the gate tools cannot be loaded at all.

`python3 gate/tools/gate.py verify` checks a project without Fusion: every STL has a stamp, every STL is an object of some project, every object is on a plate and printable, every mesh matches a valid stamp, the project's nozzle is the one the stamps were judged for, and every object prints with the fit settings the declared target says (`project.settings_match`: its own override, else the global value). Run it before telling the owner a project is ready. An object that is meant to print with another value is declared by the owner under `"objects"` of the target in `gate/project.json`; an override nobody declared is a finding, and so is a declared one that a rebuild dropped. A value that equals the target while its key is not in the list of settings that differ from the system preset is told as one INFO row: the gate cannot read a preset, so check that key in Bambu Studio once. Other rules of the `bambu-project` skill stay as they are, first of all: never write a .3mf that Bambu Studio has open.

## What only the owner decides

These files are his: `gate/owner/params.json`, `gate/owner/waivers.json`, `gate/owner/triage.json`, `gate/project.json`. Do not edit them, also not through a script. They change only through `gate.py owner ...`. Such a call shows him a prompt when the ask rules of `templates/gate/settings.json` are in the project's `.claude/settings.json`; while they are missing, `gate.py owner` refuses to write and `doctor` says so. Only he adds them. Every owner command needs `--quote`:

```bash
python3 gate/tools/gate.py owner waive F-9c1e04 --reason "the bore of the bought bearing is 1.8 mm away and cannot move" --quote "leave these two as they are"
python3 gate/tools/gate.py owner param inserts.wall_target 4.0 --tag U --source "owner, in chat" --quote "four millimetres everywhere"
python3 gate/tools/gate.py owner decide T-0003 tolerate --quote "fine for this print"
python3 gate/tools/gate.py owner decide G-5d0c1a tolerate --quote "all of these have held for a year"
python3 gate/tools/gate.py owner adopt --quote "take the model as it is today"
```

- Run one only after he said so in this conversation, and put his own words in `--quote`.
- A waiver needs a reason that says why the feature may stay. "Yes, export" is an order, not a reason, and is refused.
- A waiver holds for one feature in one state. When the feature changes it expires, and you ask again.
- A number looser than the registry default needs tag M or U. You may propose one; you may not set it.
- A fit is measured only where it was printed. `owner param NAME VALUE --tag M` stores a fit with the nozzle, material, hole compensation and layer height of the project it was printed from (`--under KEY` when several are declared). It is refused while that project file does not hold the values its target declares: the coupon was printed from the file, so run `verify` and put the two in step first. For any other project the fit counts as D, and `params explain NAME` says why. One size of a parameter (`--key`) cannot be M while its base value is not.
- A pocket for a bought part (a magnet) whose fit is D is not exported (`fits.bought_part_pocket`). Print the coupon first: a small block with the pocket in three sizes, in the real material, from the real .3mf project. Its depth along the print axis is a whole number of layers, and its faces lie where a layer ends, counted from the first layer height of the target (`print.z_layers`).
- To propose: write `gate/proposals/<name>.json` and show him the rows. Give the measured value, the limit, what stops the fix, and what the choices cost.
- Switching `mode` to `enforce` is his decision too.

## Rules and numbers

- The rules are in the plugin's `rules/registry/<category>.json`, vendored to `gate/rules/`. Each has a level: `block` (a check exists and stops the stamp), `attest` (a question), `advice` (text only; apply it by hand and say that you did or did not).
- A number is a parameter with a default, a unit and a tag. `python3 gate/tools/gate.py params explain inserts.wall_target` says where this project's value comes from and which rules read it.
- A new lesson becomes a rule in the registry of the plugin, with a judge fixture when it blocks. It does not go into a project file as prose. See the plugin's CONTRIBUTING.md.
- A rule that seems wrong for this project: tell the owner. The triage decision `rule_wrong` exists for that. Do not work around the rule.

## When the gate itself is in trouble

`python3 gate/tools/gate.py doctor` checks the interpreter, the vendored copy against `gate/LOCK.json`, the registry, the parameters, the project files, and one short ping to Fusion (a one-line read-only script with a 5 s limit: only a script that comes back shows that Fusion can run a check). It also lists every .3mf and every folder of STL files in the tree that `gate/layout.json` does not name, a document without motion groups, a key of `"stl_names"` that names no part of the last scan, a part whose STL path changed in the layout since the scan (scan again), a map the scan refuses, a file that two parts name as their STL, and missing ask rules.

- **forked**: a file under `gate/tools/` or `gate/rules/` (or the ledger tools beside them) differs from the lock. This blocks every command that judges, stamps or writes: `run`, `scan`, `export`, `verify`, `attest answer` and `owner` exit 4. `doctor`, `status`, `rules`, `params`, `report`, `triage` and `attest list` still read. Move the change into the plugin and vendor again; do not edit the lock.
- **stale**: the installed plugin is newer than the vendored copy. A warning. `python3 ${CLAUDE_PLUGIN_ROOT}/tools/setup_project.py <dir> --update-tools` brings the new rules; a judge that changed re-opens only its own check.
- **Fusion does not answer**: commands that need Fusion exit 4. `status`, `verify`, `rules`, `attest` and `run --offline` still work. Do not call into a hung Fusion a second time with a script of your own. `gate.py run` itself is safe to call again: it sends nothing while the unit it sent is still working, and it takes that unit's result when it is there.

## Adding the gate to a project

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/setup_project.py <dir> --gate
```

It vendors the tools and the rules, writes `gate/LOCK.json`, creates `gate/project.json` in `observe` mode, `gate/layout.json`, `gate/owner/params.json` and `gate/RULES.md`, and turns the print ledger on. It prints two things and writes neither: the permission rules for `.claude/settings.json` and the lines for `CLAUDE.md`. Show both to the owner; he adds them.

Then:

1. Check `gate/layout.json`: every Fusion document, STL folder and .3mf project of the project is there (`doctor` and `verify` list the .3mf files and STL folders it does not name; the empty template under `"templates"` is not listed while it holds no mesh). A generator is a script that computes a mesh itself, with no Fusion body behind it: a script that exports Fusion bodies is NOT one, and `gate.py export --generated` refuses an STL that came from Fusion. Check the declared target of each .3mf project in `gate/project.json` with the owner: nozzle, line width, material, and the slicer settings every fit is measured under (layer height, X-Y hole and contour compensation, elephant foot compensation, wall generator). `setup_project.py --gate` reads them from the file as it is saved that day; a value a tool write changed earlier is already wrong there. Declare `motion_groups` for each document in `gate/layout.json` (which components move together, for example `[{"key": "slide", "patterns": ["^Slide"]}]`): with none declared, the gap to moving neighbours is not checked for any part, and the report and `doctor` say so. A part is exported to `<stl_dir>/<part slug>.stl`. Where the project already has STL files under its own names, and a build table that reads them, give each such part its file in `"stl_names"` of the document: `{"Motor plate": "MotorPlate_rev3.stl"}`, the key being the part name exactly as the census shows it, or its part slug, the value a file name inside `stl_dir`. Without it `gate.py export` writes a second file that nothing reads, and `verify` lists the old one as unstamped for ever. The map takes effect with the next `gate.py scan`, which prints one `STL` line for every part whose file changed and one `STL NAME` line for every key that matches no part; until then `export` still writes the old path and says so in a `NOTE` line. If an earlier export already wrote `<part slug>.stl` for a part you map now, delete that file once the mapped one is exported: nothing rewrites it, and after the next change of the part it is listed as an STL without a valid stamp. A bad map stops the gate with a message (also a value that is a generator's STL). A mapped file that is another part's is a `NAME CLASH` line of the scan (exit 4): the part stays in the census with the file it had, and the map has to change. The scan prints the same line for any file that two parts name as their STL, for example two documents that share one `stl_dir` and each have a part with the same slug. `doctor` names a key that matches no part, a slug key that is never read because the part also has an entry under its name, a map the scan refuses, a part that still has its old path, and a file in the STL folder that is the part's file in another letter case.
2. `python3 gate/tools/gate.py doctor`.
3. `python3 gate/tools/gate.py run --audit`, repeated while it exits 3. An audit blocks nothing: it records what the project already has as inherited findings, and it notes the geometry of each part it judged (the baseline). It asks no question. A row that could not decide (`NOT_RUN`) is not inherited: it stays open and the list says what makes it run.
4. `python3 gate/tools/gate.py triage --md` and give the list to the owner. Its first table has one line per group (the same kind of finding on every part: rule and cause), what is measured and wrong first, with the count, the parts and the measured range. For each group he chooses: fix, waive, tolerate, or the rule is wrong (`owner decide G-...` for a whole group, `T-...` for one row; `owner adopt` takes the audit over without deciding a row). An inherited finding with no decision, or with "fix", blocks the next export of its own part. One he decided to tolerate, to waive, or whose rule he called wrong does not: the part is exported, its stamp is `pass_attested` and names the check as `tolerated`, and the report lists it.

**An inherited part** is a part whose geometry is still the one the audit judged, and that has no stamp. Nothing about it changed, so `run`, `status` and `attest list` ask none of its questions: they count them as waiting. `run --changed` does not measure it again once the owner adopted the audit. It stops being inherited in two ways. It changes: the next scan gives it a new signature, every question is asked, and a feature that changed gives new findings, which are ordinary blocks. Or it is exported: `gate.py export` asks its questions (`attest list --part ID` shows them) and holds its undecided rows; with those closed it gets its stamp like any other part. The baseline of a part is written once. Running the audit again does not make a changed part inherited again: it is then judged as in an ordinary run. Once the owner has adopted an audit, only `gate.py owner adopt` adds to what is inherited: a part that an audit judges after that (a new part) is judged as in an ordinary run, its rows block, and they go into the proposal for the owner. A proposal entry counts only while the report of its audit lists the part: a proposal written by hand makes nothing inherited. Do not answer the waiting questions of forty parts ahead of time; answer them for the part you export. A mesh of an inherited part that is already in a .3mf project is carried by the writer in both modes; only a new mesh needs a stamp.

## Reporting

Say per part: stamp written or not, the open findings, the answers the stamp rests on, the waivers used. Give the counts by verdict, not one number: a `NOT_RUN` is not a `FAIL`. Name the inherited parts as such: judged, not stamped. List the rules that are registered and not checked yet (`deferred` in the report): for those the gate proves nothing, and the old care applies. Never fold "not run" or "not verified" into "passed".
