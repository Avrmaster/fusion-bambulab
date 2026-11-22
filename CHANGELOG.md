# Changelog

What each version of the plugin changes. Skills update with the plugin; the tools copied into a project do not. Read the section of a version before you refresh a project with `python3 <plugin>/tools/setup_project.py <dir> --update-tools`, and review `git diff` afterwards.

## 0.3.2

Attest questions every ordinary part can answer honestly. In the first project under enforce, six questions had no true answer for almost every part, so nothing could be exported: only an owner waiver was left. Each fix below gives the ordinary case an honest label or an answer by facts, and keeps the case the rule is for without a passing answer. Each has tests in `tests/test_attest_honest.py` and fact fixtures in `tests/fixtures/questions`. One change is in a probe and takes effect when the part is measured again in Fusion: it is marked **re-measure**.

### Fail-open answers closed

A check of the first version of 0.3.2 on a real project found answers that let real defects pass. Each is closed again, and the ordinary part keeps its honest answer. Tests in `tests/test_attest_fail_closed.py`.

- **Standard parts by their designation.** A body is a standard part only by the designation in its own name (`gate_attest._standard_part`): a thread of the screws, washers or inserts table (`M3x12`, `insert M3x4x5`, `washer M3`), a DIN or ISO number of a screw, washer, nut or pin (the tables' own numbers and a built-in list), or the size of a wood screw or of a dowel of the pins table (`wood screw 4x30`, `dowel 8x60`, `pin DIN 6325 5m6 x 50`). A loose word is never enough: `pin`, `rod`, `shaft` or `screw` alone, or a word of the component, says nothing, and a name that speaks of a magnet, board, header, connector, belt, switch, bearing, lead screw, standoff, sleeve or spacer is none. `fits.pin_ref_pattern` is no fastener pattern for the attest answers any more (the pin judges keep it). The kind `hardware` a probe gives (for `\bpin\b`, or for a word of the component) is not taken as proof: a connector `JST XH 4 pin`, a `Pin header 1x4` or a `Rod magnet D4x10` in a component of screws keeps `hardware.bought_from_step` asked. The probe's own pattern is unchanged: a narrower one would measure every unit again.
- **hardware.bought_from_step.** The label "it touches only standard parts ..." is refused while a body whose box touches the part's box (gap up to 0.05 mm) is no standard part by its name: a board, a magnet, a belt, a switch. A body farther off within the margin keeps the question asked and is no proof against the label.
- **motors.lead_path.** A body of another kind than `hardware` is a motor by its path whatever its own name says: the `Shaft` of a stepper, or a census row `ref NEMA17 ... / shaft D5`, is the stepper (0.3.2 had dropped it as a pin). A body whose own name is that of a connector (JST, XH, Dupont, pin header, terminal, plug) is a connector, whatever `motors.ref_pattern` says. "No motor and no connector" is answered by facts only when every body near is a standard part or a bearing by its name; a hardware body under any other name keeps the question asked.
- **fits.pocket_content.** "The bought part on its axis is not fitted in this hole" is refused when the census of the part stores no radii for a body that reaches into the hole (facts measured before 0.3.2: measure the part again, or give the label or fit parameter that is true), when the body carries a fit class on the hole's diameter in its own name (`pilot D25h7 x 2` on a D25 step, `4m6x32` in a D4.02 hole: a named fit is fitted), and when the hole is not in the census. "A screw head seat" takes only a screw by its designation.
- Fixtures: the servo's plug `XH 3p` is now a connector by its name; the texts of two `no_motor_near` fixtures follow the new wording.

A second check found four more answers that let a defect pass. Closed too, each with its fail-open case and the honest ordinary case in `tests/test_attest_fail_closed.py`:

- **fits.pocket_content, one-sided room.** "Not fitted ... passes with room" is refused for a cylinder within `fits.pass_room_min` of the hole wall AND for one wider than the hole where they overlap (a D8 shaft in a D7.6 hole, a D25.6 spigot in a D25 step): a body wider than its hole is pressed in or collides, it never passes. A body around a printed boss that the census cannot tell from one in the hole takes a waiver.
- **fits.pocket_content, bodies the census cannot see.** The census reads the cylinders of solid bodies only, so a bought part drawn as a mesh, or a bought solid with no cylinder on the axis, had no row and both "no bought part goes in" and "not fitted" were taken. Both are refused now while `fusion_near` lists a mesh or bought body whose box touches the part (gap up to 0.05 mm) that is no coaxial row of the hole (new held claim `"untouched": true`, beside `by` or `coaxial_room`). The near unit gives the box of the whole part, not of the hole, so this fails closed: a fit parameter, a waiver, or a measure again after the body is moved off.
- **fits.pocket_content, named fits on the whole path.** A fit class (h7, H7, m6, k6, g6, js ...) is read on the whole name of the body, its components and what stands in parentheses (`gearbox G20 pilot D25h7 / Body1`, `gearbox (pilot D25h7)`), within 0.5 mm of the hole's diameter.
- **motors.lead_path, touching bodies.** As for the standard-parts label of `hardware.bought_from_step`, the agent's "no motor and no connector in or beside this part" is refused while a body whose box touches the part is no standard part and no bearing by its designation (and no row of the screw unit). The default `motors.ref_pattern` also knows `42BYG...`, `28BYJ-48`, `MG9..` and `SG90`; connectors by their name also include XT30/60/90, Wago, DC jack, socket or barrel, and a connector in the component of a bought body (`XT60 male / Body1`). A project with its own value of `motors.ref_pattern` keeps it: add the designations there.

### Known gaps

- regression.intended: "moved on purpose together with its mating part" has no held claim, so it is taken for a finding that says the mating part did not follow.
- Standard parts: a sensor whose name holds a screw word and a table thread (`NTC 100K screw M3`, `thermocouple bolt M6x1`) counts as a standard screw.

### hardware.bought_from_step

- Standard parts are outside the rule: screws, wood screws, washers, nuts, heat-set inserts, pins and dowels are drawn to their table sizes and have no STEP file to come from. New label "it touches only standard parts drawn to their tables (...)", answered by the gate itself with the new fact test `only_table_parts_near`: every body near or on an axis is a fastener by its own name, and no unit lists a bearing, a body it refused or an unrecognised body on a screw axis.
- The first label now covers the usual mix: "every bought part it touches is from its STEP file, or drawn from the owner's measurements where it has none; its standard parts (screws, washers, nuts, inserts, pins, bearings) are drawn to their table sizes". Motors, boards, magnets, belts, pulleys, gearboxes, bearings and meshes stay asked.
- The standard-parts label is refused while the facts show a bearing, a refused or unrecognised body, or a motor. "Not applicable" is still refused while screws are shown.
- `param` is no evidence here any more (any measured parameter was accepted). `waiver` is: the owner alone accepts a simplified board or magnet.

### fits.pocket_content

- A body on a hole's axis that lies beyond the hole's ends (census span) goes in nowhere: it is named in the text, not in `by`. A through hole with only such bodies is census.unknown_hole's question. The finding keeps its context, so its id does not change.
- New label "the bought part on its axis is not fitted in this hole: it passes with room, sits around it, or is held by a printed part in it". It is refused while a cylinder of that body lies within the new parameter `fits.pass_room_min` (0.15 mm, tag D) of the hole wall. The census now stores the radii of the coaxial cylinders ("coaxial"[]."r") (**re-measure**); facts without them refuse nothing.
- New label "a screw head seat: the head of the screw on its axis sits in it". It is refused unless a screw reaches into the hole and the hole's widest diameter seats a head of the screws table of that thread.
- A press or slip fit still needs its fit parameter.

### regression.intended

- New label "changed on purpose in place: ...". It is taken only for a finding that says the feature changed at its place ("changed", "is blind now", "goes through the part now") and names nothing that is gone. A hole still at its place under another class now says "changed: a hole is still at that place", not "is gone".
- `attest.held` takes `{"text": {"has", "not"}}` for a rule asked per finding. A question carries `finding_text`.

### assembly.order

- New parameter `assembly.cover_reach` (default: access.free_height_over_head; the owner sets the reach of his key). In access.screw_path only a body within that reach of the head top is a cover; the farther bodies are one INFO row (an L key reaches the head under them, access.key_swing judges the swing).
- New label for a cover that goes on earlier: "each cover named is mounted later and comes off first, or goes on earlier and has a key way the head is reached through: both are in the build notes". A picture is evidence too.

### motors.lead_path

- A body that is a fastener by its own name (without what stands in parentheses) or a row of the screw unit is never a motor, and a hardware body is held against its own name and component, not a component above it: "M3x12 DIN 912 (cover to base)" in a component "cover + motor screws" is a screw. A connector named "JST connector 4 pin" stays a connector.
- New label "a motor is beside this part, and its plug and leads leave away from it: ...".
- The default `motors.ref_pattern` also matches stator, rotor and stepper designations like 17HS4401. Other names of bought motors and gearboxes go into the project's own value of the parameter.

### screws.count_load_joint

- "Every load joint has at least the minimum count" is refused only when the part has neither its own screws nor screws that pass it (held `some` of `fusion_screws.screws` and `fusion_screws.passing`).

### Other

- `generated.source`: a parameter given as evidence must be of class fit.
- `gate_registry`: the held claims of a finding (`by`, `text`, `coaxial_room`, `screw_seat`) are checked.
- `tests/test_walks.py`: the parameter-as-evidence walk lends a rule the `param` kind, since no shipped part-level rule takes it now.

## 0.3.1

False classes found in the first full run of 0.3.0 on a real project, closed. Each change keeps the real case of its rule failing, and each has a fixture for the false case and one for the real case. Some fixes are in the probes and only take effect when the part is measured again in Fusion: they are marked **re-measure**. Until then the judges read the stored facts as described.

### Fixed: walls.thin_faces

- **Rings cut a few hundredths off centre.** An annular face (a relief floor, a lead-in shoulder, a step on a post) whose two circles lie 0.002 to 0.03 mm apart, from a cut placed on a rounded centre, was a "strip" because the probe skipped only rings whose circles were 0.001 mm apart. The probe now skips a ring whose circles are at most 0.05 mm and a quarter of its width apart, and lists a ring further off centre as shape `"ring"` with `"ecc"` and its narrowest side as its width (**re-measure**: `fusion_thin`). On stored facts the judge tells such a ring from its two full-circle sides, its area, and a census hole on its axis, and estimates the offset from the centroid; a ring that is really off centre still fails.
- **Modelling artefacts.** A place whose narrow faces are all narrower than the new parameter `walls.artefact_width` (0.1 mm, tag D, the owner confirms it), none of them the land of a rib and none touching a wider narrow face (bridge steps included), is one INFO row per part with the places listed, not one FAIL each.
- **Four-sided bands.** A face with four sides, the long ones equal within 5 % and the short ones within 1 %, is judged by area / long side, not by 2 x area / perimeter: a 45 degree bevel 1.202 wide no longer reads 1.18, the 1.5 x 1.2 wall of a slot no longer reads 0.67. A face that tapers is judged as before.

### Fixed: holes the census splits

- `probes/_geom.py` `stack_holes`: axes within 0.05 mm (was 0.02) are one stack, so a relief or a deepening cut 0.03 mm off a pocket's axis is one hole with it (**re-measure**: `fusion_census`).
- On stored facts the judges join the two shapes such a split leaves (`_joined_holes`): a stub of the same diameter that continues the far end of a through hole and opens into it, and a short wider step of a relief diameter in front of an insert pocket's mouth. `inserts.pocket_shape`, `inserts.mouth_reachable`, `inserts.relief_missing`, `fits.bought_part_pocket` and `census.unknown_hole` read the joined hole: no "0.64 short" pocket, no iron path that starts at the relief floor and hits the relief wall, no "what goes in" for the pieces. Two holes that each open on a face of their own are never joined. An insert-band hole that opens into another hole on its axis and is not joined is NOT_RUN ("stack not followed"), not a pocket judgement.

### Fixed: fits and print

- `fits.bought_part_pocket`: the size of a pin is read from its own name without what stands in parentheses, from an explicit `D8`, `D8h7` or `5m6` first, and never from the size of a key (`shaft D8h7 (key 3 x 3 x 14)` is D8, not D3 x 14). A bearing whose note names a shaft is no pin. The output shaft of a bought assembly is judged by its diameter fit only, with no stock-length question.
- `print.fit_axis`: a blind fit pocket whose mouth lies on the bed face is no "opens towards the bed": its ceilings are judged instead. A step from a wider to a narrower diameter that is a ring held at its outer edge only fails when it is wider than the new parameter `print.fit_ledge_max` (1.0 mm, tag D; a melt relief's own step is the insert table's), its floor when it is longer than `overhang.max_bridge`; the rest is one INFO row that says a magnet, bearing or pin floor may print up to one layer short. A pocket opening downwards above the bed still fails.
- `print.z_layers`: where the pocket lies on the bed is known, what prints decides (the layers whose middles lie in it): too shallow for the part, or more than one layer deeper, fails; the whole-layer test is kept for a pocket whose place is not known. A part height that is still a registry placeholder is NOT_RUN ("measure the part in hand"), never a FAIL. `gate_params.values()` hands such a parameter over as `name#placeholder`.

### Fixed: regression.feature_lost

- A hole that is the same void on the same line, named from its other mouth or held as several stacks, is one INFO row, judged by its diameter profile along the old hole (a filled head bore, a narrowed seat or a closed stretch still fails, as "changed: old -> new", never "gone"). Blind to through is INFO for a plain hole and asked for an insert pocket.
- A hole that moved across its axis (up to 3 mm, same segments, only a relief diameter may change) with every ref on its axis and every mating printed part moving its own hole along is one INFO row. `judge_part` hands the judge the hole lines of those mating parts (`ctx["mates"]`). A mating part that did not follow, or nothing on the axis to say why, is "moved X mm, now at P", asked.
- The rows of one screw station (a head bore and its clearance hole split by bridge steps) are one row, and an opening the hole's line crosses goes with it: silently when it moved by the same vector and kept its size (its wall change is said), else in the text of the hole's row.

### Fixed: screws and washers

- `hardware.refs_present`: a screw that stands in a hole the census lists under "partial" (broken open, a clearance or head diameter, at least half a circle, over the shank) has its hole.
- A board, magnet, sensor or bearing is never a screw by the name of its component (`fusion_screws`, **re-measure**); a body whose circles lie on several axes alike is no turned part and is never joined to a screw. On stored facts such a screw row is left out of the screw judges and `hardware.recognised` gives one NOT_RUN: a screw may be hidden in it.
- `screws.family_lengths`: a head that bears nowhere on this part and whose washer lies on another printed part is judged on that part only. The probe counts plastic under a washer only where that washer lands when it lies on another printed part (**re-measure**).
- `screws.head_seat_full_ring`: a head in a U-slot (air in one half-strip from the axis no wider than the clearance + 0.2, plastic on both flanks, each at least `washers.slot_land_min`) is a full seat. The probe now stores where its seat samples lie on material, with a ring just inside the head's edge (**re-measure**; stored facts fail as before).
- `washers.proud`: a mesh never sets the nearest neighbour; meshes are listed in `mesh_near` by their boxes, a washer of the same screw stack is not a neighbour, and a body of `access.overlay_pattern` is skipped (**re-measure**). A gap known only by a box, or a nearest body that is an overlay, is one NOT_RUN per part, never a FAIL.
- `washers.landing`: the lap a clamp washer needs is no more than the face its ring offers ((D - Da_max) / 2 outer, (da_min - d) / 2 inner, from the bearings table, with the seat from the census), judged within `bearings.contact_gap_max`. The probe also measures the lap along the bearing's own radial line (**re-measure**).

### Fixed: bearings

- `_geom_rings.seat_scan` probes the material side near the nominal radius first, so a sleeve 0.5 thick round a pin is the seat, not the pin inside it (**re-measure**: `fusion_bearings`).
- `bearings.seat`: a seat that is a taper nominal within `bearings.seat_taper_tol` where it grips gives no off-nominal INFO; a seat of bearing size with bought bodies only beyond its ends is asked what it is (INFO), not NOT_RUN "rename the bearing".

### Fixed: access

- `access.key_swing`: heads whose straight path only other measured bodies block are one INFO row (access.screw_path asks those covers); heads whose straight path is known only as a mesh are one NOT_RUN row, never pooled into the assembly question; a hidden printed part of the census is a measured body. `fusion_openings` keeps the assembly path of a swing block ("in").
- `access.hole_covered`: a bore that is a piece of the key channel of a head behind it on its axis (the head's straight path followed through it, no wall of the part between) is left to access.screw_path.
- `access.mouth_sealed`: the end of a bore that holds the head of a screw already seated in it is left to access.screw_path; the other end is judged as before.
- `access.hole_closed`: the floor of an insert pocket whose own screw comes in at the mouth and ends inside it is closed by design, whatever lies behind it on the axis (one INFO row).

### Tests

- `tests/test_guard.py` takes the temporary folder out of the printed paths before it looks for the word "gate": a TMPDIR whose name holds it failed three tests.
- `tests/test_fit_settings.py` follows the printed-depth rule of print.z_layers.

### Fixed in a second pass: no real defect silenced, no unjudged check shown as INFO

- `bearings.seat`: a tapered seat whose every height under a ring presses on that ring by more than `bearings.seat_taper_tol` (a post wider than the bore, a bore narrower than the ring, over the ring's whole width) fails. One taper drawn across two bearings is caught this way: the second ring sits on the wide end of the first one's taper. The row names the other bearing on the same taper. A taper that is nominal where its ring enters stays quiet, and a straight seat off nominal is still the INFO it was.
- `screws.head_seat_full_ring`: a U-slot is a full seat only when its flanks run on past the head. The probe now samples 32 points 1.0 beyond the edge of the head (**re-measure**: `fusion_screws`). A hole broken out of the end of a bar, where the head hangs over the end, is no U-slot and fails. Facts without the samples beyond the head fail as before.
- `washers.landing`: a clamp washer may reach over its ring only as far as the shield or seal begins (outer ring to D2, inner ring to d1) and fails when it reaches further. The bearings table gains `shields` (d1, D2) for the common sizes, with a source line: these are approximate catalogue values and tag D, so check them against the sheet of the bearing in hand. A washer past the ISO face of a bearing that has no shield row is one NOT_RUN per part.
- `clearance.min_gap_moving`: a part in no motion group (and a document with none) is NOT_RUN, which says to register the motion group in `gate/layout.json`. Before, this was INFO, or nothing at all for older facts.
- `fits.decided_not_printed`: a fit with no decision on record (a parameter without a tag, no seat fit row) is NOT_RUN. A fit decided (D) or standard (S) and never printed here is still the question.
- `fits.bought_part_pocket`: a toleranced pilot or spigot of a bought part (named with a diameter and a fit class, such as `pilot D25h7 x 2`) that sits in one step of a hole is judged as a press, slip or running fit of that step. Its row is anchored at the mouth of the step.
- `inserts.wall` and `inserts.wall_at_relief`: the probe reads a wall at the thinner end of its bisection, to 0.001 rounded down to 0.01 (**re-measure**: `fusion_inserts`). A wall drawn at a limit reads the limit, and one a few thousandths under it reads under it. On older facts, a wall within the bisection step of a limit says that it may be that much thinner.
- `inserts.no_tapped_plastic`: a blind hole of tap-drill size that is shallower than the least thread engagement of its size, with nothing on its axis, is a marker and is not asked which screw goes in.

### Re-measure after updating the tools

`fusion_thin` (every part), `fusion_census` (every part: the stack tolerance), `fusion_screws` (every part with screws, washers or a board near it), `fusion_inserts` (every part with insert pockets: the wall end), `fusion_bearings` (parts with a thin sleeve on a pin), `fusion_openings` (parts with heads beside bought assemblies). The next `gate.py run --changed` measures them again, because the probe texts changed.

## 0.3.0

The checks no longer depend on an agent remembering the project's instructions: hooks mark every change made in Fusion and hold the end of the turn until the changed parts are checked, the gate's own state can no longer be written by a tool call, an owner decision needs the owner's own words and a one-time ticket, and the .3mf writer gives no amnesty in enforce mode to what lies in the file.

### Added

- **Hooks around every tool call** (`hooks/hooks.json`, `tools/gate/gate_watch.py`; standard library, no Fusion, no judge, a few small files read). Measured on a Mac: about 35 ms per hook call with Python 3.14, about 80 ms with the system Python 3.9; a Bash call runs two (PreToolUse and PostToolUse), in every project, gated or not.
  - PreToolUse, before Bash, Write, Edit, MultiEdit, NotebookEdit and the Fusion MCP write tools:
    - a call that can change the model in Fusion (`fusion_mcp_execute` without `readOnly`, `fusion_mcp_update`, `fusion_mcp.py run ... --write`, anything that names the Fusion port 27182, a Python script file whose text calls the port or those tools) is marked in `gate/run/dirty.jsonl` BEFORE it runs, so a call that fails or hangs half way is marked too;
    - denied: a write of `gate/owner/**`, `gate/project.json`, `gate/stamps/**`, `gate/tools/**`, `gate/rules/**`, the gate's run, ticket and fit-settings files, `bambulab/ledger/**` and its tools, `.claude-plugin/` of a gated project, `.claude/settings*.json` when the hooks, plugins or permission rules change, the plugin files under `~/.claude/plugins/`, `~/.claude.json`, the managed settings and the session transcripts; `claude plugin disable|uninstall|marketplace remove`; `claude` started with `--setting-sources`, or with `--settings` that touch the hooks or plugins (`claude plugin validate` and the like pass). `gate/layout.json` only when a registered project, STL folder or document would go;
    - an STL or .3mf of a gated project written past `gate.py export` / `bambu3mf.save` (redirections, cp/mv/install/rsync/ln/ditto, zip, unzip or tar into an STL folder, `curl -o`, the slicer CLI `--export-3mf`, an FTP upload to the printer, a Fusion script that exports STL) is denied in enforce mode and warned about in observe mode. In enforce mode a Fusion script named through a variable the hook cannot read is denied ("name it by its full path");
    - a Bash command is read the way bash reads it: split into simple commands at `;`, `&`, `|`, `&&`, `||` and newlines; comments and line continuations handled; the bodies of `if` / `for` / `while`, `$( )`, backticks, `bash -c`, here-documents fed to a shell and `find -exec` parsed again; wrappers (`sudo`, `env`, `xargs`, `timeout`, ...) and keywords taken off; `$VAR` expanded from the command's own assignments and the environment, and a write through a variable it cannot read whose name is a gate file or a printed file is denied. A folder target counts for what lands in it (`cp -r x gate`, `rsync -a x/ gate/`, `cp -r /s/gate .`), and so do `git checkout REV -- gate`, `git checkout .`, `git restore`, `git reset --hard`, `git stash`, `git clean` and `git apply` of a protected path (`git restore --staged` and `git reset` without `--hard` touch the index only and pass). Interpreter code is denied when one line names a protected file (by its path, a path joined from pieces, or the name of a gate file in code that names "gate") and writes, or when it writes a path taken from its arguments and is handed a protected one; it is warned about when it only names one. Output to stdout or stderr is no write;
    - the owner quote check (see Changed).
  - PostToolUse and PostToolUseFailure: the mark when the pre-tool hook left none, and the exact `gate.py run --changed --for <key>` to run.
  - Stop and SubagentStop: the turn does not end while a mark of the session (or of that subagent) has no qualifying run after it: `run --changed` or `run --all` that started after the mark, asked Fusion (not `--offline`, not `--no-scan`, not `--audit`), came from the same session or from the owner's own terminal, and ended with 0, 1 or 2 (`gate.py` keeps every `run` in `gate/run/checks.jsonl` with its start time, flags and session). Exit 3 says "call it again"; exit 4 lets the turn end with "NOT checked". At most three blocks in a row (counted by the host's `stop_hook_active`, else by time); then the turn ends with a loud line, and the marks it let go only get one reminder line per later turn. A project whose vendored gate is older than 0.3.0 (no `gate/tools/gate_watch.py`: nothing could clear a mark) is never held: one line says its changes are NOT checked until `setup_project.py <dir> --update-tools`. A stop that is not blocked says once which guarded saves since the last stop let objects through without a stamp.
  - SubagentStart: the kept digest and the command to run go to the subagent as context.
  - A session whose own folder is not gated (a plugin-development session with the project as an additional folder) is guarded for every path of a gated project it writes. The hooks keep the gated roots they have seen in `~/.claude/plugins/data/fusion-bambulab-gate/roots.json`; a Fusion write of such a session marks each of them, and its Stop checks them.
  - When a hook check fails or runs out of time (3 s), a PreToolUse call that touches the gate's files, a printed file, the settings or an owner command is denied with "call it again"; every other call goes on with one line that says the hook failed. Before 0.3.0 every failure was silent.
  - The owner's switches, in `gate/project.json` (his file): `"hooks": {"stop": false}` (no hold, a line instead) and `"hooks": {"pre_tool": "warn"}` (a denial becomes a warning). For every project: disable the plugin, or Claude Code's `disableAllHooks` (which also drops the SessionStart digest).
- **`gate.py owner bundle PROPOSAL --quote "..." [--dry-run]`**: a prepared proposal of `param`, `target`, `waive`, `decide`, `adopt` and `mode` entries, applied with the owner commands under one quote, all of it or nothing (every read and write goes through an in-memory stage until the last entry and the end state as a whole passed; `gate/project.json` is written last). `--dry-run` prints what each entry does and a unified diff of every file the apply writes, `gate/owner/bundles.json` included, writes nothing and needs no quote. The proposal is a `.json` file or a Markdown file with one ```` ```json gate-bundle ```` block; the format is in the `gate` skill. An applied bundle is kept in `gate/owner/bundles.json` with the owner turn its quote came from. A `target` entry is the first owner command that changes a declared target. A proposal path that starts with `-` is refused.
- A declared target may hold a `"print"` block (`enable_support`, `support_type`, `support_remove_small_overhang`, `wall_sequence`; also under `"objects"` for one object): `project.settings_match` compares it like the fit settings, and a target without it is NOT_RUN. `setup_project.py --gate` reads it from the file.
- `filament` is a fit setting (`target.fit_settings`): the filament type each object prints with (the type of the slot its `extruder` names, else of slot 1), compared with the target's `material`; `build` and `set_filament` name it.
- `gate.py doctor` lists the scripts of the project that write a .3mf with `zipfile` themselves, past `bambu3mf.save` (`gate_common.zip_writers`).
- `gate/run/saves.json`: size, modification time, mode and unchecked objects of each project file after its last guarded save. The SessionStart and SubagentStart hooks say "changed outside the tools since its last guarded save: run gate.py verify" for a file that differs, and "N object(s) in <project> have no stamp: <names>".
- Every ledger `write` event holds the vertex-set hash of its mesh (`"vset"`): a weld or a repair of a stamped mesh (the same vertices, other triangles) is still backed by it.

### Changed

- **An owner command needs the owner's own words and a ticket.** The PreToolUse hook lets `gate.py owner ...` through only when every `--quote` is his: his whole newest turn, or a piece of one of his turns of at least 12 characters and two words, or the answer he clicked in a question card. An owner turn is a transcript line with `"origin": {"kind": "human"}` and nothing else in it, a `"turnOrigin"` of `human` (or none), not a sidechain, not meta, not a compaction summary, with injected blocks removed; the whole transcript is read. `owner bundle` also needs a dry run of the same, unchanged proposal and a quoted turn newer than that dry run. When the quote holds, the hook leaves a one-time ticket in `gate/run/owner_tickets.json` (the exact arguments, the quote, the proposal's sha256, the turn); `gate.py owner` run from an agent session (`CLAUDECODE` / `CLAUDE_CODE_SESSION_ID` set) refuses without a matching ticket younger than five minutes, and uses it up. So `python3 -m gate`, an import of gate.py, a glob, a symlink or a script file that calls it, a `--dry-run` hidden in a comment, on another line or after `--`, an abbreviated `--quo`, and a proposal edited after the check all get nowhere. The owner in his own terminal needs no ticket. gate.py's options can no longer be abbreviated.
- **A mesh is carried only on a stamp that verifies.** The guard counts a mesh as carried when a ledger `write` event backs it (pose, source STL geometry, or vertex set under its own name) and that event names a stamp of its own model that still verifies for the declared target. The mode the event was written in no longer matters: a journal line proves nothing by itself, and a stamp that went stale carries nothing in either mode. Meshes found in the file on disk are put on record as `"tool": "outside"` and never back anything (nor do the old `"carried"` events or `"unchecked": true` ones).
- **No amnesty in enforce mode.** A mesh that lies in the file on disk and that nothing backs is let through in observe mode only (every save says `N object(s) in <project> have no stamp: <names>`; with `"mark_unchecked": true` its plates get the prefix `[unchecked] `). In enforce mode it is judged like a new mesh and refused, also at a settings-only save or a plain re-save. `bambu3mf.plates()` / `objects()` and `ledger.plate_status` ignore the prefix; `bambu3mf.plate_name()` takes it off.
- **In enforce mode the file is held against its declared target.** Before a registered project is written, the archive goes through `project.settings_match`: a value that contradicts the declared target (global or per object, the `"print"` block too) is refused, whatever the file on disk held. A key the target does not declare, or the archive does not hold, is one loud line at every save (`verify` keeps it NOT_RUN). An empty project (a template laid first) is not compared.
- **A note covers a fit-setting change only when the new value is the declared target's** (the object's value under `"objects"` first, else the project's). A noted change away from the target, of a key the target does not say, or in a project with no declared target is judged as not asked for: refused in enforce, one loud line in observe.
- **A .3mf outside the gated tree is judged.** `bambu3mf.save` finds the gated root of a file outside every gated tree from the source STLs of the meshes it placed, from the working folder, from `CLAUDE_PROJECT_DIR`, or because a source STL has the bytes of an STL in a registered STL folder of a gated root the hooks know (a copy in a scratch folder), and judges the file as an unregistered project of that root: one loud line in observe mode; in enforce mode every mesh needs a valid stamp, nothing of it is carried, nothing goes into the journal, `saves.json` or `gate/fit_settings.json`.
- Only this plugin's own `templates/` and `tests/` are no gated project. Before, any `.claude-plugin/plugin.json` up to three folders above a project switched every hook off for it.
- `sync3mf`'s rebuild is one write: `bambu3mf.build(..., write=False)` returns the archive, the manual settings and kept positions go back into it, and it is saved once.

### Fixed

- The guard let every mesh that came in during observe mode, and everything that lay in a project file, through for ever after a switch to enforce ("observe-period amnesty"), and an appended journal line with `"mode": "enforce"` carried any mesh: see Changed.
- A fit-setting note was self-declared: a script that named `xy_hole_compensation` could set 0 in a project whose target says 0.125, and the save went through as asked.
- A .3mf written to a scratch folder from a gated project's STLs was no gated project's business.
- `gate.py owner bundle --dry-run` left the staged read/write functions in a module first imported during it, so the same process read back values that were never written; it did not list `gate/owner/bundles.json` and showed `"(dry run)"` as the quote.

### Fixed after the integration review

The three parts below were written apart and merged; a review of their seams found these. A completeness review then found the last group (from "The first export of a part" on); each of those has a test.

- **A stamp holds the face its checks were judged on.** Each judged row and each check of a stamp keep the print axis and bed height (`gate_plan.judge_inputs`, "inputs") and the tables the judges read. `gate_stamp.verify` judges a check again from the stored facts when either changed (a part turned in the slicer, a part placed on a side after it was exported while it lay nowhere, a stock list in `gate/owner/tables.json`), and the stamp is stale while that judgement fails: `print.fit_axis`, `print.relief_layers` and `print.z_layers` were never judged again for a stamped part. `judgement_out_of_date` names such rules, so `run --changed` judges them again offline.
- **A print the owner records measures nothing again.** The probes get the tables without what only the judges read (an insert or bearing `measured_under`, `seat_fit`, the screws and pins he stocks, where a measured bearing came from; `gate_deps.probe_tables`); the measured d, D, B of a bearing stay. Before, one recorded coupon made every census and inserts unit stale and every stamp stale with it.
- **A new probe text or Fusion version kills no stamp by itself.** `run --changed` still measures those units again, and a stamp goes stale only when the facts then come out otherwise, as the stamp contract says. A stale check whose facts hold no record of what they read (measured by 0.2.x) now says `gate.py run --changed, then export`.
- **A bed face measured on an STL that is not the export of the part's present geometry is "earlier geometry".** Before a re-export the STL on disk and the mesh the project places are the old geometry, so `print.bed_face` is asked for the new one, not answered by the facts. A stored "facts" answer counts for a stamp only while the facts still give it, so `status`, `export` and the stamp agree (before, status could call a part stamped and list its open question).
- **The .3mf guard pre-checks every object that lies otherwise than on disk**: a stamped part in a new pose or turned in its build item, or under other support settings (its slice key changed), not only new meshes; a save that changes a plate's slice key is held against the slice record. A file outside the tree or unregistered gets the overhang pre-check in enforce mode too (no slice record of its own).
- **A save refused after its plates were sliced** puts the slice record back as it was for the file on disk.
- The PreToolUse hook: `2>/dev/null`, `2>&1` and other descriptor numbers before a redirection no longer hide the target of `cp`, `mv`, `ln`, `install` and `rsync`; the gate's own JSON writers (`write_json`, `append_jsonl`, ...) count as writes in interpreter code; the slice records, `gate/run/bed_faces.json` and the bed-face cache are the gate's state; a writing Fusion script chained with a gate command (`gate.py verify && python3 edit.py`) leaves its mark; `mv -t`, `tar -C DIR -xf` are read, and `ln -s SRC`, `touch -r REF`, a copy of a gate file elsewhere and a scratch copy's own `gate/project.json` are no longer read as writes of the project's files; a denial names the root of the path; a scratch copy reached only through a written path does not join the list of gated roots.
- A session whose own folder is not gated: another project's enforce mode no longer denies its Fusion STL export (a warning that names that project), the Stop text starts with `cd <root> &&`, and the home files (`~/.claude/settings.json`, other plugins' data, `claude plugin disable` of another plugin) are its own business again. In a gated session only this plugin's files under `~/.claude/plugins/` and `claude plugin disable` of this plugin are denied.
- `owner bundle`: two `baseline` entries on a project with no baseline file keep both; a `baseline` entry carries the numbers it shows (`"expect"`, written by `gate.py slice` into its proposal as `bundle_entry`) and the apply is refused when the slice record moved since the dry run. The dry run of a switch to enforce warns that every save of a file with unstamped objects is refused (it said they were let through).
- Rows of `print.overhang_angle` and `print.slice_support_growth` name their project (`<key> plate N: <object>`, and the project in their context): the same part in two projects gets two ids, and one waiver covers one project. **Waivers given for these two rules before get new ids.**
- `print.overhang_angle` judges an object whose support setting is not listed from the slice of the same mesh, turn and settings when there is one; without one its NOT_RUN says what to do.
- The first `gate.py slice` of a project (or one that changes every plate) is one run of the slicer, not one per plate.
- The SessionStart digest is stale after a write of a registered .3mf or a slice (new input group `projects`). `status` says per project when it was never sliced or changed since its last slice, and an STL whose stamp no longer verifies is "stale" there too. `verify` folds the overhang rows a missing slice causes into one line, caps the rows it prints per project and counts projects in its summary.
- The bed face of each STL is kept in `gate/run/placed_cache.json` by the states of the files it reads and the code that measured it, and the slice keys of a file are read once per process: `status` no longer reads every registered .3mf again for the bed faces in each process. `verify` still reads each registered .3mf in full once per process (see Not done).
- The placed-walls cache holds the digest of the wall and mesh code and the mesh's triangles: a newer measurement, or a re-triangulated mesh on the same vertices, is measured again.
- `setup_project.py --update-tools` writes `gate/RULES.md` again and proposes the permission rules of this version, naming the rules of an older template to remove (`doctor` warns about them too); it names the command lines of `templates/CLAUDE.md` that the project's gate section does not have yet.
- **The first export of a part placed from an older STL no longer locks itself out.** Every STL of a real project comes from an older exporter, and the model changed since. Before, the export took the bed face from the old placement as measured on this geometry and answered `print.bed_face` from the facts; right after it the same frame said "earlier geometry", the answer was gone, `verify` said stale, and in enforce mode the guard refused the swap of that very mesh. Now an export judges the geometry it writes (`gate_plan.exporting`): a mesh placed from the STL still in the folder is the earlier geometry, its face is taken from that placement (both STLs are in the frame of the document), the design rules that read the print frame are judged again with it, and `print.bed_face` is asked, not answered. A part that was never stamped is "earlier geometry" once an export of its signature measured an STL that is not the file in the folder; before any export the file is taken as it is. So the first export of such a part stops once at `print.bed_face` when it writes other geometry than that file (a model changed since, or only another mesh of the older exporter); see the next section for what that costs now. Export, `verify` and the guarded swap of the new mesh then agree, and a seat the new geometry turns sideways is refused at the export.
- **The guard holds every stamp against the archive it is about to write**, not against the file on disk (`project_bed.pending`): the face each part lies on in this save, and the answers that rest on it. A part turned on its side inside the save being written is judged again (`print.fit_axis` and the other print rules) and refused in enforce mode. A file that is no registered project counts as one more placement while it is written. Only the archive being written is measured at the save; the other projects come from `gate/run/placed_cache.json`.
- **A part that two projects lay on different faces is judged on each face.** A production and a test project that share the STL folder gave `print_frame` no face at all, and `print.fit_axis`, `print.relief_layers` and `print.z_layers` passed as INFO. Now `print_frame` holds one frame per face ("frames"), every rule whose judge reads the print frame is judged once per face, every finding is kept (it names the placement), and the worst decides. `print.bed_face` is answered by the facts only when every face is measured on this geometry.
- **A row of `gate/owner/tables.json` re-judges only the checks that read that table.** The tables a judge looks at are recorded at each judgement ("tables_read", also in each check of a stamp), and only those are digested. Before, any edit re-judged every check of every stamp in memory at each guard save and `status`. On a copy of a 55-part project, a pins edit re-judges 43 of 2016 rows (0.07 s instead of 1.87 s); an edit of the screws, inserts or bearings table still re-judges a quarter to a third of them (0.75 to 0.8 s: the hole classification reads those three).
- **A unit record of a pre-release 0.3.0 build no longer kills a stamp.** Those records digested the whole tables; the probes now get them without what only the judges read, so the digest differed once by itself ("table X changed"). A record without the new tables-view mark ("tv") whose digest is still the one of today's whole table is measured again for it as a tool reason, and no stamp goes stale for it. A whole table that changed too is still "table X changed" (see the next section).
- `gate/run/placed_cache.json` also holds the digest of the code it depends on (`project_bed.py`, `project_consistency.py`, `stl_mesh.py`, `meshid.py`), and `setup_project.py --update-tools` removes it and `gate/run/placed_walls/` (a new copy of the tools starts without the verdicts of the old code).
- The guard no longer takes "the slice keys cannot be read" for "nothing turned": in enforce mode such a save is refused with a line that says why, in observe mode one loud line is printed and logged.
- **Coupons get a stamp.** `gate.py coupons` writes a coupon stamp for every coupon STL (`gate/stamps/_coupons/KEY.json`: its geometry and the declared target of KEY). The guard and `verify` take it like a part's stamp for a project declared with the same target, so the coupon plate that measured fits rest on can be written in enforce mode. A mesh with a coupon's name and another geometry has no stamp.
- **`gate.py owner table TABLE [ROW]`** writes `gate/owner/tables.json`, which no owner command wrote before: a print of an insert profile or of the seat fit (with the declared target of `--under`, checked against that project file like `owner param --tag M`), a measured bearing, the pins and screws a project stocks. `--dry-run` shows the change; a bundle entry of kind `table` does the same.
- Tests for what had none: the placed cache (a hit, and a miss after a .3mf, an STL, the memory or the code changes), the slice keys read once per file state, the `gate.py run --changed, then export` advice, and the refusal when the slice keys cannot be read.

### Fixed after the second review

Each of these was reproduced first, and each repro is now a test (`tests/test_walks.py` NotRigidPlacement, OldRecordsAndARealTableChange, UpgradeInObserve; `tests/test_bundle.py` OwnerTable; `tests/test_coupons.py` CouponPlate; `tests/test_deps.py`).

- **A scaled, sheared or mirrored build item is refused in enforce mode.** The guard reads the build items of the archive it is about to write (`gate_guard.not_rigid`). An object whose item does more than turn and move it is refused in enforce mode, whatever its stamp says: every pocket and seat would print at another size, or the part as its mirror image. Before, such a save was carried whenever `print.bed_face` had been answered with a label (a part exported before it was placed, or the first export over an older STL): the face was no longer measured, the print rules judged INFO, the overhang pre-check gave NOT_RUN, and the stamp stayed valid. In observe mode the save is written with one loud line, and the object is listed as unchecked. A remembered placement of such an item now names the problem too.
- **A real table edit on a pre-release record makes the stamp stale again.** `gate_deps.why_stale` takes the "older copy" tool reason only when the record's digest equals today's whole table (or all tables for `*`); otherwise it says "table X changed". Before, any difference on such a record was a tool reason, so a measured bearing or a shipped table change after `--update-tools` left the stamp valid on facts measured under the old table.
- **In observe mode a stamped part whose mesh did not change is no longer called unstamped.** The guard judges a mesh from the file on disk in observe mode too, when the save names its source STL: a valid stamp of that geometry backs it, as in enforce mode. Before, every observe save, `gate/run/saves.json` and the SessionStart digest listed it under "have no stamp" for ever, while `verify` and `status` called it stamped, and exporting it again did not clear the line.
- **An export that stopped at a question costs one Fusion export, not two.** The STL it wrote stays in quarantine with the job it was written for (`fusion_export` "quarantine": a digest of the parameters, tables, target and body, and the file's sha256). The next export of the same signature and job judges that file again (`gate_stamp._kept_export`); a part changed since is exported from Fusion again. `status` ("first_export") and `run` name the parts never exported through the gate whose first export can stop at `print.bed_face`.
- **`owner table`: one row, one key.** A pin diameter is stored as `%g` (`5.0` is `5`), a screw size as `M%g`, a bearing alias as its designation, and the command replaces the row under any other spelling. A file that names one row twice is refused (`gate_registry.project_table_errors`): before, the key that came last in the file was read, so `pins 5.0 --value 20` and then `pins 5 --value 16` kept the 20 mm pin in stock.
- A bundle entry of kind `table` whose `row` is a number is refused with a line (exit 4); before, it raised a TypeError, also in the dry run.
- **`gate.py coupons` run again keeps the coupon plate saveable.** The stamps of earlier runs for the key stay, each with the target it was made for (`gate_coupons.holds` reads the entry's own target). Before, a second run after one fit was recorded dropped that coupon's stamp, and every later save of the project that held the printed plate was refused in enforce mode with advice that cannot work for a coupon. A coupon no run has on record now says to take it off the plate or make the coupons again.

### Not done

- The hooks are a first line, not a sandbox: an agent that writes a script file which edits the gate's files, the ledger or the ticket file directly is not seen (the guard at the next save, `verify` and the owner's review remain). Managed-settings deny rules for the protected paths (installed by the owner with sudo) are the layer an agent cannot undo; that and the switch to enforce are owner steps.
- Whether a turn sent into a session by another session or a scheduled task is ever recorded as `"origin": {"kind": "human"}` was not observed; only `peer`, `task-notification` and `host_synthetic` origins were seen in real transcripts, and those never count.
- A conditional mode entry ("enforce once every part of the next print is stamped"), an owner entry that adopts one unstamped mesh as printed, and a SessionStart line naming the plates the guard renamed.
- A ledger event is tied to its stamp by the model hash; that the event's pose is a placement of that model is not proven at every save (it would mean reading the stamped STL each time).
- PreToolUse denials were checked against the documented hook contract, not watched in a live session in every permission mode.
- A Fusion write of a session whose own folder is not gated still marks every gated root on the hooks' list, by design: Fusion is one shared app, and which document the write touched is not known before it runs (review item UP-1).
- `verify` reads each registered .3mf in full again in every process (`project_consistency._read_project`): only the bed faces and the slice keys are kept between calls (review item F8, partly done).
- Review item S2-10(e) is still a heuristic; it was not changed in this pass.
- A row of the screws, inserts or bearings table re-judges every check that classifies holes (the classification reads those three tables whole); a record by key inside a table is not kept.
- The bed face of a part that was never stamped is taken from the STL in the folder until its first export: `status` and `run` can answer `print.bed_face` from that placement before the export finds that the model changed since. The export asks it then (once per such part: on a project of about 55 parts with older STLs, about 55 answers), and no stamp rests on the earlier answer. `status` and `run` now say which parts this can be, and the second export uses the STL in quarantine. Answering it from the facts of the old placement was not done: that face was measured on another geometry, and the rule's label says it was judged there.
- The 0.7 s digest budget is still not met on a 55-part project: the digest takes about 0.8 s in-process, `status` about 0.9 s, before and after these fixes (it judges every part again). The narrowing by table is correct (no row changed its findings while its tables_read named neither the table nor `*`, over all six tables).
- A pre-release record (no "tv") whose whole table changed only in what the judges read (a recorded print, a stock list) makes its stamp stale until `run --changed` measures it again; it cannot be told from a real change.
- A coupon stamp is bound to the geometry and the target it was made for, not to a plate: a coupon may go into any project with that target. Earlier coupon STLs stay in `gate/proposals/coupons/KEY/` (they are not cleaned up).

### Changes for a project after `--update-tools`

- New file `gate/tools/gate_watch.py`. The hooks run from the plugin, not from the project: they are on as soon as the plugin is updated. Until the project is refreshed, the Stop hook never holds a turn and says that changes are NOT checked.
- New files under `gate/run/` (git-ignored): `dirty.jsonl`, `checks.jsonl`, `hook_state.json`, `saves.json`, `owner_tickets.json`.
- **Enforce refuses every save of a project file that holds a mesh without a valid stamp**, a settings change included. Export every part of the project through the gate and swap it in before the owner switches to enforce (`gate.py verify` lists the unstamped objects).
- **Every stamp written by 0.2.x is stale after this update**: judges changed, and facts without a record of what they read are measured again. A project in enforce mode cannot save its .3mf until that is done: switch it to observe before `--update-tools`, or run `gate.py run --changed` (with Fusion) and export every placed part before the next save. The stale rows say `gate.py run --changed, then export`.
- Waivers of `print.overhang_angle` and `print.slice_support_growth` rows get new ids (the rows name their project now): the owner gives them again.
- `gate/run/placed_cache.json` and `gate/run/placed_walls/` are removed by `--update-tools` and measured again at the next `status` or `verify`.
- A stored judgement is judged again (offline) the next time its part is judged: the print frame it was made with now lists its faces, and the new row records the tables its judge read. No stamp goes stale for it: a stamp's checks hold the same print digest for a part on one face, and a check without "tables_read" is held against the whole tables as before.
- A project that ran a pre-release 0.3.0 build: its unit records are measured again by the next `run --changed` (the tables view), as a tool reason; no stamp goes stale for it.
- A part placed from an older STL: its first `gate.py export` stops at `print.bed_face` (exit 2) when it writes other geometry than that STL (the model changed since, or the older exporter only meshed it otherwise). Answer it, and export again: the second export judges the STL in quarantine, Fusion is not asked again. `status` and `run` list these parts.
- A `gate/owner/tables.json` that names one row twice (`"5"` and `"5.0"`, a bearing alias next to its designation) stops the gate: the owner runs `gate.py owner table` for that row once, which keeps one key.
- In enforce mode a save with a scaled, sheared or mirrored object is refused: set the scale back to 100 % in the slicer first.
- `setup_project.py --update-tools` rewrites `gate/RULES.md` (generated) and prints the permission rules to add and the rules of the 0.2 template to remove (the broad `Bash(*gate.py * owner *)` ask, ask rules for edits of `gate/owner`); the owner edits `.claude/settings.json` himself.
- A `baseline` entry of an owner bundle needs `"expect"` (the numbers the owner was shown).
- In enforce mode a save whose file contradicts the declared target is refused: declare what the project prints with first.
- A script that notes a fit setting away from the declared target is refused in enforce mode: declare the value first (`owner bundle` with a `target` entry).
- Owner commands from an agent session need the hook's ticket: run them as plain `python3 gate/tools/gate.py owner ...` commands; `owner bundle` after a dry run the owner answered.
- Declared targets without a `"print"` block get one NOT_RUN row of `project.settings_match`; without a `material`, the new `filament` key is NOT_RUN as well.
- `gate.py doctor` names scripts that write a .3mf with zipfile.

### Privacy and side effects

- The quote check reads the session transcript the host names in the hook input (`transcript_path`), only its owner turns; nothing of it is written anywhere except the uuid and time of the turn a quote came from (in the ticket and in `gate/owner/bundles.json`).
- The hooks write under `gate/run/` of a gated project, and the list of gated roots they have seen in `~/.claude/plugins/data/fusion-bambulab-gate/roots.json` (`FUSION_BAMBULAB_GATE_INDEX` moves it).

### Tested

- Without Fusion: every hook event fed the JSON a host sends (`tests/test_watch.py`): marks before the call, after a failure, from script files and for sessions of another folder; the Stop block, exit 3 / 4, offline / no-scan / audit / other-session / earlier runs, the cap and the reminders after it, an old vendored gate, the owner's switches; denials and warnings per file kind, mode and shell form (multi-line commands, if / for, substitutions, comments, folder targets, git, variables, joined paths, argument paths, slicer CLI, printer upload, plugin switches), read-only commands and stdout idioms, plugin templates; quotes against human, card, assistant, sidechain, peer, task, synthetic, compaction, injected and meta lines, short pieces, the dry-run order and tickets; a hook that fails. The bundle from an agent session: no ticket, a used ticket, an edited proposal, `-m gate`, a glob, `-- --dry-run`, a comment, an abbreviation. The guard: no amnesty in enforce, an enforce journal line that proves nothing, stale stamps, welds backed by the vertex set, the target held in enforce, files outside the tree found by STL bytes, working folder and environment. Python 3.9 and 3.14.

### Part B: a change is checked incrementally

#### Changed

- **`run --changed` measures what changed since the part was last CHECKED, not since its stamp.** Before, a part with no stamp counted as changed, so in a project where nothing was exported yet every `run --changed` measured every unit of every part again (the whole document for one edit). Now every unit stores what it read (below), and after the scan a unit is measured again only when something it read changed: its own part (signature, place, shown or hidden), a body in a region it read (changed, moved, came, left, shown or hidden), a parameter, table or job key it read, or the text of its probe. A unit whose stored facts still hold is taken over as done ("reused" in the plan) and costs no call, stamp or no stamp. The parts a run reports are the same as before (every part without a valid stamp, less the owner's settled ones), plus every part with a unit to measure again, so the exit code and the report still show every open row of those parts; only the measuring is incremental. A change of nothing measures nothing (the run is a scan and a judgement from stored verdicts).
- **A neighbour that changes reopens the parts that read it.** A unit of an unchanged part whose region holds a body that moved or changed is stale and runs again, also for a stamped part or a part of the owner's adopted list. Before, a moved neighbour could close a hole of a stamped part unseen.
- `gate.py status` says the parts in three states: `N changed, not checked` (with the first reason per part: `fusion_openings: Cover:1 changed, moved, or was shown or hidden`), `N checked, no stamp`, `N stamped`. The line `N changed since their stamp` is gone. `status --json` has `unchecked_why`, `checked_ids`, `stamped_ids`.
- A stamp is also stale when the facts it rests on were measured again and came out otherwise (new key `"facts"`: a digest per Fusion probe; a stamp without it: a Fusion probe entry stored after the stamp), or when a unit it rests on would be measured again because something it read changed.
- After the first call of a plan, a run judges only the parts whose units finished in that call; a judge whose facts, parameters and ctx are what it judged before is not run again (`"input"` beside each stored judgement); the judgements of one part are written in one file write, not one per rule.

#### Fixed after review (same batch)

- **`gate.py export` scans first.** It scans the part's document (one short call; `cmd_export` scans each document once per call and prints what the scan found), and refuses (exit 1) while a unit of the part would be measured again because what it read changed: "what the part was checked against changed since it was measured (fusion_openings: Cover:1 changed, moved, or was shown or hidden)". Before, an export stamped a part on the census of the last scan, so a neighbour moved after the run could close a hole of a part that got a valid stamp. The "already stamped" branch goes through the same scan and test (it still asks Fusion for the signature). A document that is not open is exit 4. The `NOTE` about a changed `stl_names` map before the next scan no longer appears in practice: the export's scan makes the map take effect.
- **A plan measured over several calls is confirmed by one more scan.** The call that finishes such a plan exits 3 (`NEXT 0/1`, "the next call scans once more ..."); the next call scans, and its plan measures what changed while the units ran (usually nothing: no unit is sent). Before, the plan finished with exit 0 and `status` said "0 changed" on a census as old as the plan's first call. For `--fresh` the confirming plan takes the facts just measured over.
- **A ref's state includes its shape.** The scan gives every solid ref `"shape"`: 16 hex over the area and centroid of each face (as a part's signature has it), remembered for the Fusion session; a ref with more than 3000 faces uses its revision id. A mesh ref gets a digest of its node coordinates and triangles. A hole that moves inside a bought plate or washer (same volume, area, counts and box) now re-checks the parts that read it. Hidden printed parts are refs too and are covered by the same field.
- **Bodies cannot hide behind one key.** A mesh body is `<occurrence>|mesh:<name>`; a second body under one key is `<key>#2`, `#3`, ... in the order of the scan. Before, the later body overwrote the earlier one in the census and its changes were never seen.
- **A unit reads the census by its record.** When the census unit of a part is measured again, a unit whose record names the job key `features` is looked at again after it, whatever its probe (not only the fixed list `NEEDS_CENSUS`).
- **Facts that depend on the clock or on Fusion are measured again.** Facts with `screws_cut` or a `late` row (a look cut short by time) are stale. Every envelope gives `fusion_version`; the scan stores it per document, and a unit measured in another version of Fusion is stale. A Fusion update therefore costs one full measurement.
- **Stored judgements are taken over only from the same judging code.** The key of a stored judgement now also holds a digest of `gate_judges.py`, `gate_features.py`, `gate_common.py`, `probes/_geom.py` and `probes/_geom_rings.py` (module constants, public helpers, finding ids, the classification of holes) and of the rule itself (its fix order and texts). `judgement_out_of_date()` compares the same digest. `run --fresh` judges every part again without the cache. Before, a plugin update that changed `_GRID` or `context_hash` left old findings and ids in place.
- **The code hash of a unit ignores comments and docstrings.** `gate_deps.code()` hashes each file of a unit with its comments, docstrings and blank lines cut out (by token and node positions, so Python 3.9 and 3.14 give the same hash). A text-only edit of `_fusion.py` no longer makes every unit of every project stale. A change of code in a shared file still does: say so before releasing it (below).
- **Short units share a call.** A unit whose last measurement is known (`work_s` now stored beside its facts, the sum over its calls; older entries: `elapsed_s`) starts in what is left of a call when 1.5 x that + 1.5 s fits, with what is left as its deadline. A packed call that comes back unfinished is no stall; one whose facts say a look was cut short is measured again in a call of its own. A unit that took most of a call, or several, still gets a call of its own.
- **A unit given up over its budget stays given up** while nothing of the document (a digest of every body), its inputs, its probe or Fusion changed: it is no longer sent again (two waits of budget + 10 s) on every run. `--fresh` tries it again.
- `gate.py status` prints the time of the scan its counts rest on, and how many units are to be measured again with their Fusion work by their last measurement (`status --json`: `measure_again`). `gate.py verify` prints the time of the scan the stamps are verified against. It still asks nothing of Fusion: the `.3mf` writer calls it, and the export is the step that makes a stamp.
- Tests: the fake Fusion can answer with facts that are a function of the model (a neighbour that touches the bracket closes its window); the equality test of an incremental and a full run now changes only a neighbour, judges the full run from nothing, and fails when the neighbour comparison or the facts digest of the judge key is taken out. New tests for each fix above.

#### Added

- `gate_deps.py`: the record of what a unit read (`deps` beside its facts: probe text, parameters, tables and job keys it read, its regions and every census body in them with its state) and `why_stale()`.
- The census keeps every body of a document after each scan (`docs[key]["world"]`: one line per body with its state, kind, face count and box in the document frame).
- `gate_plan.stale_units()`: which units of which parts must run again and why.
- `gate.py run --fresh`: measure every unit of the chosen parts again (for a deliberate full check, and to compare with an incremental one).
- A unit planned only because the census of its part is measured again is looked at once more after the census unit (`"recheck"`): when the holes and openings came out the same it needs no call.

#### Fusion side (needs a live check: probe text changed)

- `probes/_fusion.py`: `gate_read(box, margin, solid_only, printed_faces_max)` notes a region a call read; `gate_world()` notes its own walk unless a probe passes `read=False`; `GateInputs` watches what a probe asks of `JOB`, `JOB["params"]` and `JOB["tables"]`; the envelope gets `"reads"` and `"inputs_read"` (beside the facts, never in them). `gate_asked()` plus a check in `run()`: a body a probe asked a question of that lies in no noted region is noted as a region of its own, with the note "the regions this unit noted did not hold ...: report it".
- `fusion_scan`: `"shown"` (every light bulb on the path) on parts and refs, and `"copy_bodies"` (the further occurrences of a part with their pose and box). Nothing of the parts list changes.
- `fusion_census` notes its 2 mm neighbour walk. `fusion_inserts` notes one cylinder round each pocket (levels, radius plus wall) instead of the whole part box. `fusion_openings` notes the box of each path, the look for screws (part box + 1 mm and the candidates + 0.5 mm), the slab behind a cap and the look under a screw head, instead of the 72 mm walk. `fusion_bearings` notes the candidate look (25 mm, solids, no printed body over 80 faces), per bearing the bodies beside it (2.5 mm), and per clamp the look where it can land, instead of the 120 mm walk. Facts of every probe are unchanged.
- `fusion_screws`, `fusion_gap` and `fusion_near` note their whole walk (45, 5 and 10 mm).

#### Faster

- Measured on a copy of a real project (44 Fusion parts, 352 units, 752 refs) with a fake Fusion that replays its stored facts, notes the regions the new probe text notes (modelled from the stored facts), and charges every unit its cost in the last full check (1140 s of Fusion work in 386 calls; 33 min wall with the re-judging):
  - nothing changed: 0 units, 4.3 s on the host plus the scan (about 5 s in Fusion);
  - one part changed, over all 44 parts: median 62 units and 289 s of Fusion work (p90 636 s, max 739 s for the link that carries the gearbox; 36 to 102 s for the parts of the forearm and the drive); 33 min before for any of them;
  - one ref moved (a quarter of the 752 refs): median 26 units, 172 s; a screw of a plate: 17 units, 15 s; a bearing: 57 units, 307 s;
  - after an incremental run the facts and the judgements of every part equal those of a `--all --fresh` run of the same model (byte for byte on the judged findings).
  - With the review fixes (short units share a call, one confirming scan after a plan of several calls), replayed at the default `--wall 20` with each unit's real cost split over its real calls: a median single-part change is 62 units, about 282 s of Fusion work in about 28 calls of the command (before packing about 70), p90 55 calls, max 62 (the dense gearbox link: 141 units, 750 s); about 6 to 7 min wall at the median and 15 to 17 min at the max with 2.2 to 3.9 s of overhead per call. A dense link: 22 calls, 5 to 6 min. An arm part: 8 calls, about 2 min. A screw of a plate moved: 11 calls, about 3 min. A bearing moved: 26 calls, about 7 min. Nothing changed: 1 call (the scan). The first run after the update: 352 units, 1150 s of Fusion work in 83 calls (386 before), about 22 to 25 min. The Fusion-side compile of each unit script inside a call is not modelled (70 unit scripts at the median).
- `run --offline` on all 49 parts: 19.2 s before, 3.0 s now (judgements written once per part; the bearing designations of the attest test compiled once). `stale_units()` over the whole project: 0.56 s.

#### Not done

- `fusion_screws` still depends on everything within 45 mm of the part: it is the largest part of what a single edit measures again. Narrowing it (per screw: its axis line, the gap search radius, the washer neighbourhood) needs a reading of every look of the probe.
- Plans resumed per request are as in 0.2.4.
- `gate.py verify` does not scan (it is the `.3mf` writer's check and asks nothing of Fusion); it says which scan its answer rests on.
- Regenerating ref occurrences at the same places (new keys) and hiding or showing a body re-check every unit whose region holds them. That is correct (the keys and the visibility changed) but costs time; the skill says so. A comparison by shape and place instead of by key was not built.
- Every release that changes the code (not the comments) of `_geom.py`, `_geom_rings.py` or `_fusion.py` makes every unit stale once in every project: about 20 min of Fusion work for the 44-part project. Release such changes together.
- A bearing measured for several parts is still measured by each of them.
- The first `run --changed` after the update measures every unit once: facts measured before this version hold no record of what they read, so nothing proves that their neighbours are still what they measured.

#### Changes for a project after `--update-tools`

- New file `gate/tools/gate_deps.py`. The next scan writes the bodies of each document into `gate/census.json` (one line per body). The first run measures everything once (see above), in the background as one loop until the exit is not 3; from then on a run measures what changed.
- Facts files get `deps` per probe and `input` per judgement; stamps written from now get `facts`.

#### Privacy and side effects

- Nothing new is sent anywhere: the regions and keys a unit read come back in its own result file on this machine.

#### Changed (lead)

- `templates/gate/settings.json`: agents are DENIED direct edits of `gate/owner/**` and `gate/project.json` (no permission prompt for the owner); only `python3 gate/tools/gate.py owner ...` asks. The broad `Bash(*gate.py * owner *)` ask rule is gone: it matched ordinary commands and prompted the owner. `setup_project.py` proposes the deny and ask lists that are missing.

### Part C: overhangs, supports, fits, pins, pockets, bearings and screws become block rules

Part C1: overhangs and supports become block rules that are decided by measurement.

#### The bed face is measured on the saved project

- New `tools/gate/probes/project_bed.py`. For each object of a registered `.3mf`, it takes the mesh as placed: the stored vertices, turned by the build item's own transform. It proves that this mesh is the part's STL moved as a rigid body. The fast path compares triangle centres in file order. The fallback tries the 24 quarter turns of the vertex set. Every point must land within 0.005 mm, and a mirrored or scaled copy is never accepted. The bed face is the face that this motion sends down, expressed in the STL frame.
- `project_consistency.orientations()` now reads the saved project first. It reads a build table only for a project that does not place the part yet. Each row has `"source"`: `project`, `project, earlier geometry` or `table`. `gate_plan.print_frame()` returns `"from"` and names the file, object and plate.
- When an STL has been exported again but not yet swapped into the project, the last measurement of the mesh that is still placed is used. It is stored in `gate/run/bed_faces.json` and keyed by pose hash, so it holds only while that exact mesh is still there.
- This fixes the . `print.z_layers`, the bridge-layer test of `access.hole_closed` and `print.bed_face` were NOT_RUN because the build tables are computed. They now get a bed face.
- `print.bed_face` gains a fact test, `bed_face_measured`, with a new label. A placed part is answered by the facts. A part that only a build table names is still asked.

#### The slicer is the judge of supports: `print.slice_support_growth` is a block rule

- New `tools/gate/probes/project_slice.py`. It copies the `.3mf` into `gate/run/slice/<key>.work/`, runs the Bambu Studio CLI on the copy (`--slice N`, no login, safe while the GUI is open) and reads every `plate_<n>.gcode`.
  - Support is the extruding moves under `FEATURE: Support` and `FEATURE: Support interface`.
  - Support is attributed per object through `; OBJECT_ID:`. The label order in the header is accepted only when every object height agrees with result.json within one layer plus 0.05 mm.
  - Switched-off objects are left out.
  - Support printed under no label on a shared plate is not guessed.
  - The G-code is deleted after reading. The record is written to `gate/run/slice/<key>.json` and keyed by object name and pose hash.
- New command `gate.py slice [PROJECT] [--changed | --all | --plates 1,4]`. `--changed` is the default and slices only the plates whose meshes differ from the record. The command prints each object's support. It writes `gate/proposals/support_baseline.json` with what is waiting for the owner. It never writes `gate/owner/`.
- New owner command `gate.py owner baseline PROJECT "OBJECT" ... --quote "..."`. It copies the numbers from the last slice of the mesh the project holds now into `gate/owner/support_baseline.json` (`projects.<key>.<object name>`). It never takes a typed number. It goes through `cmd_owner`, so the same ask rules and the same `--quote` apply.
- `print.slice_support_growth` is now block, stage project, judged at `verify`. It is no longer deferred, and the old plate-number key is gone.
  - Never sliced, sliced from another mesh, or a failed plate: NOT_RUN.
  - No baseline: INFO if the object got no support at all, otherwise NOT_RUN until the owner records a baseline.
  - FAIL on support where the baseline has none.
  - FAIL when support grows by more than `overhang.support_growth_share` OR more than `overhang.support_growth_abs`. That is the registry's wording and the stricter of the two; the old project script used "and".
  - FAIL when support ends more than `overhang.support_top_growth` higher.
- Before a write, `gate_guard._check` calls `gate_slice.before_write()` through `_slice_line` (one call, added after `_remember`).
  - A save that brings new meshes is held against the record. It prints one line only when a support grew.
  - With `"slice_before_write": true` in `gate/project.json` (the owner's switch, off by default), the changed plates of the archive are sliced inside the save. In enforce mode, a support that grew, or a changed plate that cannot be sliced, refuses the save. An object with no baseline is not refused there; `verify` holds it.

#### Overhang pre-check: `print.overhang_angle` is a block rule

- New `tools/gate/probes/mesh_overhang.py`, a stdlib-only check of a mesh in its bed frame. It finds three things:
  - Regions of down-facing faces flatter than `overhang.max_angle`, with the bottom on the bed left out.
  - Hanging ridges: an edge between two faces that are steep enough, rising into both, whose own direction is flatter than the limit. This is an incident: two 45 deg faces meeting at 11 deg.
  - Faces that face straight down and are wider than `overhang.max_bridge`, measured as the least convex width.
  - Faces modelled exactly at the limit pass (0.01 deg float tolerance).
  - Positions are given above the bed and from the part's low corner, so the owner can find them in the slicer.
- `project_consistency.measure()` adds `"overhangs"` (the pre-check of every object as it lies on its plate, plus the object's support setting) and `"slice"` (the facts above). On the real projects, every project is measured in about 1 s.
- `print.overhang_angle` is now block, stage project, judged at `verify`.
  - Supports on for the object: INFO, and the slice judges it.
  - Supports off: FAIL, one row per kind, with the worst value and limit.
  - Not known whether supports are on (for example, `enable_support` is not among the settings that differ from the preset): NOT_RUN.
  - Facts measured with other limits than today's parameters: NOT_RUN.
  - Only the owner waives a row.

#### Not done in this part

- Plan step 12(e): a bed-face lead-in check, and `print.z_layers` extended to relief, seat and clamp-room depths.
- Plan step 12(f): the first baselines for every plate of every registered project. Only the owner records these, with `gate.py owner baseline`. `gate.py slice` writes the proposal. The old `support_baseline.json` of the project is plate-keyed and was not migrated.
- The project side was not touched: a project's own slice-and-compare script should become a wrapper of `gate.py slice <key> --plates ...`.
- The Stop hook that runs `gate.py slice --changed` in the background belongs to batch A's hooks. The command is ready for it.
- The other plugin false classes in `plugin_false_classes.json` (inserts, walls, washers, bearings, access, regression) are not part of C1.

---

Part C2: fits measured where they print, pins, pockets that are not round, bearings, screws, and walls on the meshes a project prints from. Plan steps 13 to 18, and the false classes of the gate found in a full check of a real project.

#### Fits count only where they were printed (step 13)

- Every row of `rules/tables/inserts.json` carries `measured_under` (nozzle, material, X-Y hole compensation, layer height; `null` while never printed; a list for several prints). `rules/tables/bearings.json` has a `seat_fit` row that does the same for the seat fits. Pocket kinds take their size from fit parameters, and those already carry `measured_under`.
- `inserts.pocket_shape`: exact pockets of a row whose prints do not include the declared target FAIL with "profile measured under other settings". There is one row per kind and part. Without a declared target the row is NOT_RUN. As shipped, the M3x4x5 row records no nozzle, so it matches no target until the owner records a print.
- `bearings.seat`: when the seat fit records prints and none matches the target, every printed seat FAILs "measured under other settings".
- `fits.bought_part_pocket` keeps failing pockets whose parameters are D here, including an M parameter measured elsewhere, as before.
- `project.build_table_differs` reads the fit settings a build script writes as plain literals (with `ast`; the script is never run) and FAILs each one the saved project holds otherwise. Settings that come from an unpacked dict or a name get one INFO row.
- `fits.decided_not_printed` is answered from the facts. It now has a judge (`fits_decided_not_printed`, design stage, asked per finding). It lists the fits the part relies on that are D or S here: pocket parameters, pin fits, insert rows and the seat fit. "None" is refused while that list names anything (`attest.held` `{"by": "none"}`). With nothing on the list, nothing is asked.
- New command `gate.py coupons --for KEY`. It writes the coupon plate of exactly those fits to `gate/proposals/coupons/KEY/`:
  - One closed STL per coupon: an insert block with the row's profile, a magnet or pin pocket in `fits.coupon_steps` sizes 0.05 apart, a tapered bearing bore and post with their lead-in, and a box pocket.
  - `coupons.json`, which says what each hole is and how the owner records the result.
  - It is stdlib only and needs no Fusion and no `.3mf` write. On a copy of a real project it took 0.3 s for 7 coupons.
- New owner file `gate/owner/tables.json`, laid over the shipped tables by `gate_registry.load_default()`. `gate.py` hands the project root over with `gate_registry.use_root()`, and a vendored copy finds its own project. It holds:
  - stock pins and screw lengths;
  - the measured bearings of the batch in hand (d, D, B, source, date);
  - prints of an insert row or of the seat fit (every setting, source, date).

  A row that cannot be read stops the gate (exit 4). The registry hash covers the file.

#### Pins, dowels and rods (step 14)

- New table `rules/tables/pins.json`: DIN 6325 / ISO 8734 / ISO 2338 diameters and length series, written from reference knowledge, with `passage_over` 1.0. A project lists its own stock in `tables.json`.
- `rules/tables/pockets.json` gains a `pins` section. A hole with a pin, dowel or rod on its axis is that pin's pocket: its own name matches the new `fits.pin_ref_pattern`, and no insert or screw stands there. `fits.bought_part_pocket` judges it, with one row per pocket that names every problem:
  - the diameter must be the press, slip or running fit of the pin (new `fits.pin_press_extra`, D, 0.0; slip and running as before, per material);
  - that fit's parameter must be M or U here;
  - the length the name gives must be a stock length, and longer than a blind pocket.

  The pin's diameter comes from its name (D5, 5 x 20, 4m6x32), else from the one table row the hole fits. Pins named without a length are one NOT_RUN row per component. A hole 1.0 or more over the pin is a passage, and it is asked.
- Insert rules leave pin pockets alone, so the ring-pin seats of a lid are no M3 pockets. This closes .
- `fits.pocket_content`: "no bought part goes in" is refused while the finding names a bought body on the axis. Questions now carry the finding's `by`.
- `bearings.shaft_sleeve` is a block rule. FAIL when an inner ring is seated directly on a bought shaft that is closer than `bearings.shaft_under_bore` to the bore. A printed sleeve drawn as a ref (named by `bearings.sleeve_pattern`) is no shaft.

#### Pockets that are not round (step 15)

- `pockets.json` takes several rows per bought part (new `magnet_d6`, with `sensors.magnet_d6_*`) and a `boxes` section: board, switch, plug channel and M3 nut slot. Each takes its two sizes from fit parameters (new `sensors.board_*`, `switches.body_*` / `pocket_*`, `motors.channel_height`, `fits.nut_*`; `motors.channel_width` is now of class fit).
- A census slot or window of a box's size is that pocket, and FAILs while its parameters are D. The placeholder sizes are the registry's; a project sets its own.

#### Bearings (step 16)

- `bearings.seat_form` (any | tapered) and `bearings.seat_taper_tol`. With "tapered", a straight seat FAILs, and so does a taper that is neither `seat_taper` nor `seat_taper_thick_base`. The default "any" keeps today's behaviour. The owner's rule is "tapered", and he sets it.
- `bearings.clamp_room_washer` is split from `bearings.clamp_room`. It is used for clamp rows with "by": "washer" and read by `washers.landing`. The default is 1.0, the old value, not loosened. A project's proposal of 0.3 is its owner's decision.
- The measured bearing of the batch in hand (from `tables.json`) replaces d, D and B in `bearings.seat`, `bearings.clamp_room` and `bearings.shaft_sleeve`.
- `bearings.insertion_path` is a block rule, judged from the seat facts (`entry_d`, `entry_open`). A wall beyond the ring face that is not the seat itself and clears the ring by less than `bearings.pass_clearance` FAILs.
- `bearings.no_plastic_in_clamp` is a block rule. A printed clamping body over a ring FAILs, and only the owner waives it.
- `bearings.sleeve_end_and_preload` is a block rule. A printed sleeve or spacer, as the clamp or as the stop of a clamp, FAILs. On a copy of a real project this names a printed inner-ring spacer between two bearings; its waiver belongs to the owner (batch A's owner bundle).

#### Screws (step 17)

- `screws.family_lengths` is a block rule. It FAILs a family that `screws.family` does not name: set screws and wood screws are families of their own. It also FAILs a length outside the stock list (`stock_lengths`, `stock_lengths_set`), and a name whose length differs from the model.
- `screws.head_recess` is a block rule. A counterbore head outside 0.5 to 2 under the face FAILs. Only heads that bear on this part are judged.
- `screws.head_seat_full_ring` is a block rule. A head without a washer that bears on part of its ring only FAILs.
- `hardware.refs_present` is a block rule. These FAIL:
  - an insert pocket with no insert ref on its axis;
  - a screw hole with nothing on its axis (a hole on the line of another hole that has its screw is a piece of that channel);
  - a screw ref in no hole and no opening of the part.

  The label "clearance hole for a screw that is not modelled yet" is gone from `census.unknown_hole`.
- `access.key_swing` is a block rule. `fusion_openings` now sweeps the long arm of an L key over every seated head: 12 directions, `access.key_short_arm` above the head top, a path of radius `access.key_side_clearance / 2` and length `access.key_long_arm`. The paths use the probe's own resumable path machinery and cursor.
  - At least `access.key_swing_deg` free in one run passes.
  - Less, but with a free straight path, is INFO.
  - Neither is a FAIL.
  - Facts of the older unit are one NOT_RUN row per part.
- `screws.count_load_joint` takes an owner waiver as evidence.

#### Walls on the meshes a project prints from (step 18)

- New `tools/gate/probes/placed_walls.py` and the new project-stage block rule `walls.placed_stl`. At `verify`, `stl_mesh` and `stl_walls` measure the stored mesh of every object of every registered project. The results are cached in `gate/run/placed_walls/` by pose hash and report width.
- One process spends at most 20 s on meshes not measured yet. The rest are one NOT_RUN row, and the next verify goes on.
- The rows of the export rules are carried, with one row per object and inner rule, and they hold until the part is exported again.
- On a copy of the real project: 2 calls measured every object (24 s, then 16 s); after that, verify takes 4.7 s.

#### False classes of the gate closed (plugin_false_classes.json)

| Fix |
| --- |
| Ring-pin seats are pin pockets. |
| A straight through channel longer than the deepest pocket of its diameter is no insert pocket. |
| A hole with only a wood or self-tapping screw on its axis (new `screws.wood_pattern`) holds no insert. |
| Floors of blind holes the census knows are no slivers (one INFO row). |
| Bridge steps of counterbores whose census holes are held by their inner ends are paired (both ends of a through hole are tried). |
| A washer that lies on another printed part is judged there. |
| The far mouth of a through slot counts. |
| A stacked roller that the probe did not take for a bearing is a twin, by its designation and its contact on both rings. |
| A through hole of a bearing outer diameter in a part thinner than the bearing is asked, not called an empty seat. |
| A ring face touched by nothing while a hidden part stands near is NOT_RUN ("show it"), not FAIL. |
| The washer's own heel landing is no proud neighbour. |
| A washer with no screw has no head stack. |
| A head that bears on another part gets no under-head row here. |
| The bridge skin of the part that carries the head is not judged again on the thread part. |
| A blind pilot shallower than the least thread engagement is no screw hole. It needs no key path and is closed on purpose. |
| An empty blind recess needs no key (one INFO row). |
| A through opening named by its other mouth, or with a moved mouth, is not lost. |

Still open: a toothed profile whose chamfer joins all teeth into one region (one row the owner waives), a window whose outline moved beyond the match tolerance, and five classes that need probe changes in `fusion_openings` (what lies behind a floor, slot axes on slanted faces, channel segments, seal sampling).

#### Behaviour that changes for a project

- Exact M3x4x5 pockets FAIL "measured under other settings" until the owner records the print of the coupon under each target in `gate/owner/tables.json`. Without a declared target they are NOT_RUN.
- `verify` gets `walls.placed_stl` rows. The first one or two calls measure, and later calls are about 4.7 s on a real project.
- New FAIL rows on a real project, all of them true by the rules as written:

  | Rule | Rows |
  | --- | --- |
  | `screws.head_recess` | 36 |
  | `hardware.refs_present` | 27 |
  | `screws.family_lengths` | 27 (wood and set screws, M3x18/30 outside the shipped stock) |
  | `screws.head_seat_full_ring` | 13 |
  | `bearings.shaft_sleeve` | 9 |
  | `bearings.no_plastic_in_clamp` | 8 |
  | `bearings.sleeve_end_and_preload` | 4 |

  The owner's own stock lists and family text (`tables.json`, `screws.family`) remove the rows that are only about his stock.
- The owner should set or record:
  - `bearings.seat_form` "tapered";
  - `bearings.clamp_room_washer` 0.3 (his proposal);
  - his screw and pin stock;
  - his coupon prints;
  - `screws.family` with set and wood screws, if he keeps them.

#### Not done

- 12(e) from C1: lead-in checks.
- 14(d) is partial: the pin length against the stack in OTHER parts is not measured (only stock length, and longer than a blind pocket).
- 16(a): "thick base" is not measured. A taper of either `seat_taper` or `seat_taper_thick_base` is accepted. Telling them apart needs a probe measurement of the material behind the stop.
- 17(e) and 18 on the project side: a project's own screw recess, hardware presence and insert wall scripts should become wrappers of the gate (no project was touched).
- There is no owner command that writes `gate/owner/tables.json`: the owner edits it, or an `owner table` action is added to batch A's owner bundle.
- Box pockets are matched by size. A missing pocket (a plug channel never cut) is not seen.
- The coupons command takes every part of the census, not only the parts of one project.

---

Part C3: the 44 false-finding groups of the full check, sorted into general classes. Each class is recognised from facts (never by a part name), has a fixture built from the real case with neutral names, and has a twin fixture that proves the true finding of the same rule still FAILs. Where the facts cannot tell the false case from the true one, the row now ASKS; it is never passed.

#### The classes and what decides them

| Class | Recognised by | Verdict now | Fixtures (real case / twin) |
| --- | --- | --- | --- |
| A key or tool channel read as an insert pocket | a straight through hole longer than the deepest pocket of its diameter (`_long_channel`) | ASK: one `inserts.pocket_shape` INFO row with `census.unknown_hole` (an insert ref on its axis may lie inside it: the facts hold no position); no wall, relief or floor row | `inserts_pocket_shape__ask_long_channel_with_the_insert_measured_beyond_its_end`, `inserts_pocket_shape__ask_long_through_channel_with_nothing_on_its_axis` (C4: an insert of unknown place is NOT_RUN, one measured inside is a pocket) / `inserts_pocket_shape__fail_short_straight_hole_with_an_insert_is_a_pocket`; `inserts_relief_missing__pass_long_channel_with_the_insert_measured_beyond_its_end` (C4) / `inserts_relief_missing__fail_same_pocket_in_a_short_hole` |
| Wood-screw holes and countersinks read as insert pockets | only a wood or self-tapping screw ref on the axis, no insert ref (C2) | no row | `inserts_pocket_shape__pass_wood_screw_hole_is_no_pocket` / `inserts_pocket_shape__fail_hole_with_a_wood_screw_and_an_insert_ref` |
| Steel pin seats read as insert pockets, their webs as insert walls | a pin, dowel or rod ref on the axis (C2) | judged by `fits.bought_part_pocket` (NOT_RUN while the pin names no length) | C2's pin fixtures |
| Floors of known blind holes read as slivers | the face is the floor of a census hole at its depth (C2) | INFO | C2's `walls_thin_faces__info_floor_of_a_known_blind_hole` / `..._fail_disc_that_is_no_hole_floor` |
| Bridge steps the census did not pair | the far end of a through hole is tried as the mouth (C2) | INFO | C2's fixtures |
| Bridge steps with leftover faces touching them | `_thin_bridge_split`: the faces of one zone that are treads or risers are counted as steps | INFO for the steps; the other faces (a step cut 0.01 below the floor) stay ONE FAIL place | `walls_thin_faces__fail_leftover_faces_touching_bridge_steps_stay_one_place` / C2's square-void and no-census fixtures |
| A toothed profile whose chamfer joins every tooth | `_thin_ring`: the one region maps onto itself, cluster by cluster, when turned by one tooth (N >= 12, the largest N) | ONE profile FAIL row, which a waiver of the profile can hold | `walls_thin_faces__fail_teeth_joined_by_a_chamfer_are_one_profile_row` / `walls_thin_faces__fail_ring_with_one_tooth_that_differs_stays_one_region_row` |
| A clamp washer whose landing is another part, a missed slot mouth, a heel landing, a headless spacer | the washer's landing and screw facts (C2) | judged where it lands, or no row | C2's washers fixtures |
| Rollers stacked ring on ring read as touching both rings | a hardware cover with this bearing's designation, d and D, on both ring faces (C2) | no row | C2's fixtures |
| A bore of a bearing outer diameter in a part thinner than the bearing | through, and thinner than the bearing width (C2) | ASK (`census.unknown_hole`) | C2's fixture |
| A hidden part at a ring face | the unit lists a hidden body near (C2) | NOT_RUN "show it" | C2's fixture |
| A head that bears on another part, the bridge skin of the head part judged on the thread part | seat shares and the head part's own INFO row (C2) | no row here | C2's fixtures |
| Blind markers and spigot sockets read as tap pilots | a blind pilot shallower than the least thread engagement (C2) | no access row; the hole is still ASKED once by `inserts.no_tapped_plastic` ("not a thread: a pin, dowel, magnet or alignment hole of that size" is its answer), so the skip is no silent pass | C2's `access_hole_covered__pass_marker_too_shallow_for_a_thread` / `..._ask_tap_hole_deep_enough_for_a_thread_under_a_disc` |
| Empty lightening pockets read as openings that need a key | the unit looked inside a blind opening and found nothing (C2) | INFO | C2's fixtures |
| The floor of an insert pocket read as a cap a screw must pass | `_floor_of_pocket`: the census hole is an insert pocket, its screw is not seated on this part and its head bears on the very body the probe put "behind" the cap, and the own path out of the mouth meets that body on the axis within `access.cap_max` | INFO, one row per part | `access_hole_closed__info_floor_of_pockets_whose_screws_come_from_the_mouth_side` / `access_hole_closed__fail_pocket_floor_with_the_head_on_this_part_and_a_base_behind`, `..._fail_pocket_floor_with_no_body_measured_at_the_mouth` |
| A breakout of a hole read as a slot covered by the part | `_breakout_of`: an empty opening whose mouth lies inside a broken-open census hole (arc under 1), between its ends and within its radius there, with the size of its breakout (smaller side 0.5 d to d, larger side at most 2 d) | INFO; the hole's own path is judged | `access_hole_covered__info_slot_where_a_head_channel_breaks_out_of_a_face` / `..._fail_slot_over_a_whole_hole_is_an_opening_of_its_own`, `..._fail_slot_much_narrower_than_the_bore_it_opens_into` |
| One channel across a gap, its pieces read as sealed holes | `_acc_facing`: a sealed end faces another hole end of this part on its axis, ahead and looking back, and every sealing body is another solid body before it | ASK: one `assembly.order` row per part (a clevis with a tongue without a hole looks the same, so it is never passed) | `access_mouth_sealed__ask_key_channel_across_a_bore_with_a_tube_in_it` / `access_mouth_sealed__fail_the_same_hole_with_no_mouth_across_the_gap` |
| A body that only clips the edge of a mouth read as a seal | NEW probe measurement `profile` (C4; it replaced C3's `mouth_share`): how the section fills in front of the mouth | no row when the body covers a sliver at the rim and fills the section only farther out; a plate flat on it or a shaft lying across it seals; facts of the older unit keep the old FAIL | `access_mouth_sealed__pass_body_that_clips_the_edge_of_the_mouth_seals_nothing` / `..._fail_body_flat_on_the_mouth_covers_it_all`, `..._fail_older_facts_without_the_share_at_the_mouth` |
| "Window is gone" after a deliberate change | the same through opening by its other mouth (C2) | no row | C2's fixture |
| A through window that moved or changed size a little | `_moved_opening`: same shape, parallel axis, the old centre inside the new one | still ASKED (`regression.intended`), but as "changed: it is ... now, 0.55 off", not "gone" | `regression_feature_lost__ask_through_window_that_moved_a_little_is_changed_not_gone` (a window that is really gone stays "gone"); C2's `..._fail_through_window_that_is_blind_now` |
| The bed face unknown | measured on the saved project (C1) | judged | C1's fixtures |

#### Code

- `gate_judges.py`:
  - `_long_channel`, `_channel_row`; `_no_insert_in` and `_not_a_pocket` ("channel") use it, so `inserts.relief_missing`, `inserts.wall`, `inserts.wall_at_relief` and `inserts.floor` leave the channel to the one asked row of `inserts.pocket_shape`.
  - `_sym_axis`, `_thin_ring`, `_thin_place_of`, `_thin_bridge_split` for `walls.thin_faces`.
  - `_floor_of_pocket` for `access.hole_closed`.
  - `_breakout_of` for `access.hole_covered`.
  - `_acc_facing`, `_seal_share` (C4: `_seals_mouth`) for `access.mouth_sealed`, which now ends with `_acc_grouped` (the assembly-order row of the channel mouths).
  - `_moved_opening` for `regression.feature_lost`.
- `probes/fusion_openings.py`: every measured hit that begins within 0.5 mm of the mouth gets `mouth_share` (C4: replaced by `profile` and `full_window`). A failure leaves it out and the judge keeps `share`.
- No registry or threshold change.
- `skills/gate/SKILL.md`: one line per class.
- `tests/test_walls_calibration.py`: a face that does not belong to the bridge steps is still the FAIL row of its place; the steps beside it may now be counted as steps.

#### Not done, and why

- The seal of a body that only clips a mouth is fixed in the probe, not in stored facts: it needs a live scan of the part (Fusion, read only) with the new `fusion_openings`, and a check that the `profile` stays near 0 at the clamp pockets and is full at once for a plate flat on a mouth (C4).
- The empty-blind-recess INFO and the stacked-roller recognition by designation pass on what the unit measured and a hardware designation; I left them as C2 built them.

---

Part C4: the review of batches C1 to C3. Each finding was checked against the code; the real ones are fixed with tests, the rest say why below.

#### Supports and overhangs (C1)

- **A slice is an object's slice only under its slice key** (`probes/project_slice.slice_keys`): the pose of the stored vertices, the turn of the build item (a hand turn in the slicer lives there, not in the vertices), the object's whole block of `model_settings.config` and the process settings that move support (`SUPPORT_SETTING`). `changed_plates` and `facts_for` compare the key: an object turned on its plate or a changed support setting is NOT_RUN "turned on its plate, or a support setting changed" and its plate is sliced again. A record without keys (an older gate) is NOT_RUN "slice it again".
- **Where the slicer put support is kept**: `parse_gcode` follows X and Y and keeps the support per object as 1 mm cells with their top z, counted from the low corner of the object's walls (`origin`, `cells`, `cells_cut` per object of the record).
- **`print.overhang_angle` decides supported objects by the slice, never by the setting**: every region of the pre-check (`all`: [x0, y0, x1, y1, lowest z, width], now for every region, not only the 12 largest) is held against the cells of the slice with the same key. Support under it: INFO. None under it (the slicer's threshold angle, "on the build plate only", a manual type, small regions it drops): FAIL "the slice leaves N of M regions with no support under them", or "the slicer puts no support under it at all". No slice of this key: NOT_RUN. A face region narrower than one line (2 x area / perimeter under `target.line_width`) prints from the layer below; a region under the middle of the first layer prints from the bed. `_support_of` names the support type and "on the build plate only" in its text.
- **The overhang pre-check runs before a .3mf write** (`gate_slice.overhangs_before_write`, called by `gate_guard._overhang_line` for the objects a save brings): loud in observe, refused in enforce, for what hangs with supports off or what a slice of that very mesh leaves bare.
- **The save path of `slice_before_write`**: a slicer that is missing or cannot start, a locked record or a timeout makes the changed plates "NOT SLICED" (refused in enforce, loud in observe), never a silent write. Each plate gets `"slice_timeout_s"` of `gate/project.json`, 240 s by default (`SAVE_TIMEOUT`), not 1800. `gate_guard._check` runs the slice and overhang lines before it remembers the fit settings, so a refused save leaves the memory of the file on disk.
- **Concurrent slices**: each run works in `gate/run/slice/<key>.<pid>.work` and merges its plates into the record under `<key>.lock` (`_locked_update`; a lock older than 10 minutes is taken over). The pending archive of a save is `<key>.<pid>.pending.3mf`.
- `run_cli` decodes the CLI's output as UTF-8 with replacement: a log in another encoding is no traceback.
- **The remembered bed face follows a later turn** (`probes/project_bed`): the memory keeps the motion R (STL frame to stored frame) and the item's turn; the face is computed again from R and today's turn. A memory without R (an older gate) is not used.
- **`print.bed_face` is not answered by the facts for an "earlier geometry"**: the new geometry was never placed, pre-checked or sliced. The question fixture became `questions_bed_face_measured__asked_the_project_still_places_the_geometry_before_the_export`.

#### Access (C2, C3)

- **`access.key_swing` sorts what blocks each direction** as `access.screw_path` sorts a path. The probe keeps `blocks` per direction ({name, kind, at, share, approx, part}); a graze under `access.min_blocked_share` and an owner-named overlay block nothing; the part itself is a FAIL (only when it also blocks the straight key); another solid body goes into ONE `assembly.order` question for the part; a mesh, box or hidden body is NOT_RUN per body. Facts without `blocks` are NOT_RUN for a head that is not free all round. A swing measured with another short arm or a thinner side clearance than today's is NOT_RUN.
- Swing paths meet a mesh body by its box only (`mesh_by_box`): a mesh hit is never a measurement, and an STL ref beside twelve paths per head is no longer walked node by node.
- **`access.mouth_sealed` judges a seal by the `profile` in front of the mouth** ([mm from the mouth, share] up to the first full section or one path radius plus 1 mm, `full_window`): full within the seal gap plus 0.25 (a plate flat on it), or full within the seal gap plus the hole radius while it covers 0.3 of the section at the mouth (a shaft or rod lying across it): sealed. A body that covers a sliver at the rim and the whole section only farther out seals nothing. The window follows `access.seal_gap`, so a raised gap is judged over its own width. `mouth_share` is gone (it was never measured live).

#### Inserts (C2, C3)

- **A wood or self-tapping screw takes only a THROUGH hole out of the insert rules**: a blind hole with such a screw forms its thread in this plastic and is judged as the insert pocket it looks like.
- **A long straight channel with an insert ref on its axis**: the census now measures where each coaxial body lies along the axis (`coaxial[].span`, `_geom.coaxial_cylinders`). An insert ref beyond the far end keeps the channel asked; one inside it makes the hole the straight pocket it is (`inserts.pocket_shape` FAIL and every insert rule); one of unknown place (an older census) is NOT_RUN in `inserts.pocket_shape` and the wall, relief and floor rules judge the hole.

#### Bearings and washers (C2)

- **New block rule `bearings.spacer_room`** (judge `bearings_spacer_room`, probe `fusion_bearings`, no probe change): between two coaxial bearings, a body that covers the facing faces of both inner (or both outer) rings within the contact gap is a fixed length. Both stacks fixed: FAIL. One fixed and the other stack stopping at both rings with less than `bearings.clamp_room` of room: FAIL with the room. Nothing seen in the other stack while a mesh or hidden body is near (not named by `access.overlay_pattern`): NOT_RUN.
- **Lead-in of a tapered seat**: `seat_scan` (and so `fusion_bearings`) measures where the seat wall ends on the entry side and the chamfer there (`lead_in`, `entry_end`; `seat_lead_in` of `_geom_rings`). With `bearings.seat_form` "tapered" a seat without a `bearings.seat_lead_in` x 45 lead-in FAILs; facts without it are NOT_RUN. With "any", a straight seat more than `bearings.seat_taper_tol` off its bearing (inside the tolerance) is an INFO row.
- The `bearings.seat_taper` advice text now says what the judge checks: a post nominal at the end the ring enters and wider at its stop; a bore nominal at its stop and wider at its mouth.
- **A stacked bearing by name**: a cover counts as the twin of a bearing by the designation in its name only when the name matches none of `bearings.sleeve_pattern`, `washers.ref_pattern` or ring, plate, disc. "686 spacer ring" or "shim washer 686" locks the rings and FAILs.
- **A clamp washer's lap over its ring**: `fusion_screws` measures `ring_overlap` (the longest radial run of the washer's face on the ring, 16 directions, ends found to 0.02); `washers.landing` FAILs a lap under `washers.ring_overlap_min` (2.0, not changed; the judge allows the probe's 0.02 step). Facts without it are ONE NOT_RUN per part.
- `fits.decided_not_printed` also names the seat fit for a bore the census classes "band" near a bearing size.

#### Print orientation and layers (new block rules)

- **`print.fit_axis`** (judge `print_fit_axis`, probe `fusion_census`): insert pockets, pockets and pin pockets of bought parts and bearing seats lie within `print.fit_axis_deg` (1.0, new) of the print axis, and a blind one opens away from the bed. A part no project places yet: ONE INFO row (`print.bed_face` is asked at its export).
- **`print.relief_layers`** (judge `print_relief_layers`): the melt relief of an insert pocket along the print axis prints `inserts.relief_depth_min` to `max` deep in the target's layers, counted as the slicer cuts (a layer is open where its middle lies in the relief). A step wider or deeper than a relief of the inserts table is a spot face, not a relief.
- `clearance.min_gap_moving` names the sliding clearance (`fits.sliding_per_side`, per material) in the text of a gap between two printed parts, so a slide is waived on a measured number.

#### Walls of placed meshes (C2)

- A mesh whose walls were measured over a share of its surface is measured again by a call with more time (no more "+5 s" that no call could reach); `GATE_WALLS_BUDGET=120 gate.py verify` gives the walls of placed meshes 120 s, and one mesh may take up to 600 s.

#### Fixtures and tests

- New fixtures: `access_key_swing__*` (8, rewritten in the new form), `access_mouth_sealed__fail_shaft_lying_across_the_mouth`, `..._fail_a_raised_seal_gap_is_judged_over_its_own_width`, `bearings_spacer_room__*` (5), `bearings_seat__fail_tapered_form_post_without_a_lead_in`, `..._not_run_tapered_form_lead_in_not_measured`, `..._info_straight_seat_off_nominal_inside_the_tolerance`, `bearings_holding__fail_spacer_named_after_the_bearing_is_no_stacked_bearing`, `clearance_min_gap_moving__fail_printed_slide_under_its_sliding_clearance`, `fits_decided_not_printed__asked_a_bore_near_the_seat_size`, `inserts_pocket_shape__not_run_long_channel_with_an_insert_of_unknown_place`, `..._ask_long_channel_with_the_insert_measured_beyond_its_end`, `..._fail_long_straight_hole_with_an_insert_measured_inside`, `..._fail_blind_hole_with_a_self_tapping_screw_stays_a_pocket`, `inserts_relief_missing__fail_long_channel_with_an_insert_of_unknown_place`, `..._pass_long_channel_with_the_insert_measured_beyond_its_end`, `print_fit_axis__*` (5), `print_relief_layers__*` (4), `print_overhang_angle__fail_supports_on_but_the_slice_leaves_the_roof_bare`, `..._fail_supports_on_and_the_slicer_puts_none_at_all`, `..._fail_support_under_the_roof_that_stops_far_below_it`, `..._not_run_supports_on_and_no_slice_of_this_mesh`, `..._info_regions_narrower_than_a_line_print_from_the_layer_below`, `..._fail_a_region_wider_than_a_line_with_no_support`, `washers_landing__fail_clamp_washer_laps_its_ring_by_0_6`, `..._not_run_clamp_washer_lap_not_measured`.
- `tests/test_supports.py`: slice keys (a turn, a setting), plate errors and timeouts, two slices and the lock, the cells of the G-code, the slice deciding the shelf (INFO, bare, too low, build plate only, none at all), no slicer refusing the save in enforce, the overhang pre-check before a write, the remembered face after a turn, the re-measure of partial walls.
- `tests/test_geom_rings.py`: the fake-Fusion units measure the `profile` of a plate, a clip and a shaft over a mouth, the `blocks` of a swing (the part, a mesh by its box) and a clamp washer's `ring_overlap` (2.0 and 1.2); `seat_scan` measures the lead-in of a sharp post, a chamfered post, a post that ends 4 mm out and a chamfered bore.
- `tests/fake_slicer.py` writes walls round each object's footprint and support lines where FAKE_SUPPORT boxes say.

#### Not fixed, and why

- Support inside holes, channels and pockets (finding 15): the slice now keeps where support stands, but holding it against the holes needs the census holes of each placed part at the project stage (`verify` holds only the project's facts); that plumbing belongs with the plan of batch B. Until then a support inside a channel shows only through `print.slice_support_growth` and the owner's look.
- Thick base beside a seat (finding 13, part): the probe does not measure the material behind the stop, so either taper is accepted, as before. `bearings.seat_form` "tapered" is the owner's switch, and no project file was written.
- Two facing channel mouths (finding 9): no fact tells a key channel from a clevis whose tongue has no hole (the sealing body has no opening on the channel's axis in either case); the row stays one `assembly.order` question.
- A sleeve against its fit ladder (finding 21): needs a ladder table with `measured_under`, an owner decision; `walls.one_line` still FAILs every one-line sleeve until the owner waives it with the ladder.

### Fixed while merging A, B and C

- Every command loads the rules with the project it works on (`gate_registry.load_default(root)`), so the owner's own table rows (`gate/owner/tables.json`) are laid over the tables the same way at `run`, `export`, `verify` and in the stamp check. Before, a stamp could be called stale at once ("the tables changed") when a command ran in another folder than the project.
- `gate.py owner bundle` takes `baseline` entries (support baseline from the last slice), so a support baseline can ride in the same owner decision as the rest.

## 0.2.4

### Fixed

- **The SessionStart digest was left out in a large project.** `gate_report.digest()` judges the stored facts of every part again; with some fifty parts and hundreds of open rows that takes seconds, the hook had 0.7 s, and every session started with "the digest took longer than 0.7 s and was left out" instead of the state of the gate. Now the gate keeps the digest: `gate.py status` and every command that can change the state (`scan`, `run`, `attest`, `export`, `verify`, `triage`, `owner`, `params`) write `gate/run/digest.json` when they end, with the state of its inputs taken before the digest was computed (`gate_digest.py`: sizes and modification times of the census, the facts, the owner's files, the answers, the stamps, the registered STL folders, the run in progress, and the code and rules that built it). The hook reads that file and checks the same inputs in milliseconds: when they are unchanged it prints the kept digest; when they changed it computes the digest now if that fits its budget, else it prints the kept digest with one line that says when it was built and what changed since (the scan, the facts, the run in progress, ...). Only a project with no kept digest yet gets the old one-line message, which now says that `gate.py status` builds it. A `run` or `scan` that needs more calls (exit 3) does not rebuild it: that would judge every part in every short call. Measured on a copy of a project with 49 parts: the hook takes 0.04 s with python3 and 0.09 s with /usr/bin/python3 (it was left out after 0.7 s); a stale digest is printed in about 1 s; `gate.py status` builds it in 1.4 s.
- **The insert ref and the pocket named two different inserts, and the wall row said something else.** A ref named "insert M3x6x5" on the axis of the exact pocket of an M3x4x5 made the classifier take the pocket for an M3x6x5 in its band (rows of the same diameters are told apart by the insert named on the axis); its walls were measured 6 mm deep, into the boss behind it, and `inserts.wall` said 0.92 mm. Now (`_kind_named`): `inserts.pocket_shape` says that the hole is the exact pocket of one insert and the ref names another, and that one of the two is wrong, also when the hole is too short for the named insert (the row keeps its measure, limit, `by` and context, so its id and any triage item stay); `inserts.wall` gives NOT_RUN for that pocket (its walls were measured for the wrong insert) instead of a wall; `inserts.wall_at_relief`, `inserts.relief_missing` and `inserts.floor` say nothing about it. With the ref named after the pocket, every rule judges it as before.
- **A clamp that preloads a bearing stack was a plain FAIL.** A turret top screwed to its tube across 1 mm of clamp room presses the outer ring of the upper bearing; the tube stands off that ring by the whole gap on purpose, and the load reaches the tube through the balls, the inner ring, a spacer and the lower bearing. `screws.shell_bears` failed those screws as air a wall bends into. The probe cannot follow that path, so the row stays a FAIL (same text, context and id) and now asks the new question `screws.load_through_bearing` (level attest): the owner's answer that the screws preload a bearing stack closes it. It is asked only when one part presses the bearing and the other stands off it by at least the whole gap: a part that stands off by less (a lip that falls short of the ring) stays the plain FAIL.

### Added

- Finding rows of a part placed off the document origin also say where the place is in the document: `"world"` beside the anchor (the census matrix of the part applied to the anchor point), ` (in the document: (x, y, z))` on the status and run lines and in the report. The anchor, the id, the waivers and the answers stay in the part's own frame.
- `fusion_scan` reads, in its first call, the documents the document links (`Document.documentReferences`: name, the version it uses, the newest version, `isOutOfDate`) into `"references"`; the census keeps them per document, and the scan, the status and the digest say `OUT OF DATE <doc>: it uses the linked document 'X' in version 26, the newest is 33 ...` before any row: the parts of that document are measured as the old version holds them.

### Not done

- `screws.shell_bears` on a ring that one part's lip presses beside the screw axis at another radius, outside the slab of the gap ("Nothing bought stands in the gap within 8 mm" while a bearing stack does carry the clamp): the probe would need to follow bodies beyond the gap, which needs a measurement in a real Fusion. Still a FAIL.

### Changes for a project after `--update-tools`

- New file `gate/tools/gate_digest.py`; `gate/run/digest.json` appears after the first `gate.py status` (gate/run/ is git-ignored).
- Judges changed (`inserts_pocket_shape`, `inserts_wall`, `inserts_wall_at_relief`, `inserts_relief_missing`, `inserts_floor`, `screws_shell_bears`): `gate.py run --all --offline` judges the stored facts again. The new question is asked at the part's next check, and for an inherited part at its export.
- The `"references"` of a document appear with its next scan.

### Privacy and side effects

- The hook prints the text of `gate/run/digest.json` as it is: a file the gate wrote in that checkout. It runs no code of the project, as before.
- `fusion_scan` reads the names and version numbers of the linked documents: no download, nothing written.

### Tested

- Without Fusion: the kept digest printed when it holds, printed with the stale line per group of inputs (census, facts, decisions, run, STL folders, code), giving way to one computed in time, left out with one line when none is kept; `gate.py status` and `verify` keep it, a `run` that needs more calls does not; a broken or foreign file is no kept digest. Kind mismatch, stack question, world coordinates and linked references each have fixtures or tests and their twins; each guard removed on a scratch copy failed its tests. Python 3.9 and 3.14.
- In a real Fusion (macOS): `fusion_scan` of a document with one linked document, 1 call of 5 s, `"references"` read with its name, the version in use, the newest version and `isOutOfDate`.

## 0.2.3

### Fixed

- **A full check of a part with thousands of faces took hundreds of calls (more than an hour) because Fusion ran in App Nap, not because of the probes.** Measured in a real Fusion on a toothed part with thousands of faces: after a minute or two in the background with nothing to do, macOS puts Fusion into App Nap, and the first script after the pause runs on the slow cores from its start to its end: a point test 21 ms instead of 2 ms, a walk over the faces 12.7 s instead of 1.6 s, a plain Python loop 6 times slower. The script right after it is fast. A run whose calls are some seconds apart (a person or an agent reads the output of each `gate.py run`) met the slow state in every call, so the heavy units of that part took dozens of calls each, and calls of `fusion_thin` and `fusion_openings` ran over their budget. Now every unit (`run()` of `tools/gate/probes/_fusion.py`) tells macOS for the length of its call that Fusion does work a person waits for (`NSProcessInfo beginActivityWithOptions:reason:` through `ctypes`: user initiated, latency critical, idle system sleep still allowed) and ends that when the call ends, also after an error. With it the first call after 200 s of idle ran at full speed. On the same part: `fusion_bearings` 2 calls instead of dozens, `fusion_thin` 5 calls inside the budget with 30 s pauses between them instead of dozens, several over budget.
  - Where the activity cannot be begun (no `libobjc`, the call fails) the unit runs as before and its envelope has a note that says so. Off macOS nothing is done.
  - The point tests, their order, the steps and the cursors are unchanged, so the facts are byte for byte those of 0.2.2: checked live on that part, `fusion_bearings` in 2 calls against the stored facts of the slow run, and every row of `fusion_thin` that the 0.2.2 run had measured before it was stopped.

- **A countersunk wood screw's hole was read as an insert pocket.** A 90 degree countersink over a D4.2 hole with a countersunk wood screw on its axis fits no clearance row of the screws table, and D4.2 lies in the band of an M3 insert: `inserts.pocket_shape` failed it ("its lead-in is D8.4, the table says D4.4"), and `inserts.relief_missing` and `inserts.wall` judged the pocket the insert probe measured in it (on one part of the first audit 20 + 20 + 16 false rows). Now `classify_stack` (`probes/_geom.py`, `_geo_countersunk_seat`) classifies a stack in an insert band as a screw seat (`"table": "screws", "role": "countersink"`, the screw row that fits, else the largest thread the hole takes, match `"band"`) when ALL of these hold: its first segment is a cone deeper than an edge chamfer (0.6 mm) with the countersunk head angle of the table (within 10 degrees), the cone ends on the diameter of the cylinder behind it, a body named as a screw lies on the axis, and no body named as an insert does. Names are read from the body's own name (after the last " / "): a component that bundles screws, inserts and washers names inserts. A pocket the insert probe measured in such a hole is not judged by the insert rules (`_not_a_pocket`: "screw_seat"; `inserts.floor` included). The wide end of the cone must also be the countersunk head of that thread within 1 mm, so a 90 degree lead-in of an insert pocket (narrower than the head) stays a pocket. The same hole with nothing on its axis, with an insert named there, with a 60 degree cone or with only an edge chamfer stays a wrong pocket.
- **A tapered bearing seat was read as a wrong seat.** `bearings.seat` compared the median diameter of a seat with the bearing: a post tapered on purpose from 80.0 where the ring enters to 80.5 at the shoulder that stops it (next to a thick base) read 80.35 and failed with "tolerance 0.3". Now a seat whose median is off by more than `bearings.seat_d_tol` is accepted when it is a tapered press fit inside its band, judged end by end (`_seat_tapered`): a post nominal (within `bearings.seat_d_tol`) at the end the ring enters, at most `bearings.seat_taper_thick_base` wider towards its stop, and not narrower at the stop than at the entry; a bore nominal at its stop and at most `bearings.seat_taper` wider towards its mouth. Both parameters were in the registry and are now read by the rule; a value higher than the default needs tag M or U (`"looser": "higher"`, was null), and their descriptions now say which way a post and a bore taper. The probe's seat now carries `"rows"`, the seat diameter at each of the nine heights it already measured (no point test more), so a new run judges the ends from the profile; stored facts without it are judged from the measured wall just past the ring face on the entry side and the median, and a bore is then never taken as tapered. The seat as a whole (its median) must lie from the bearing's size to size + the band, and every height of the profile from size - `bearings.seat_d_tol` to size + the band. A straight post 0.35 over, a post wider than the band, a taper turned the wrong way, a hollow or a groove between nominal ends, a bore that narrows between its ends, and a post with no wall measured at its entry stay FAIL. Known limits: every post is allowed the thick-base band (0.5 by default), whether or not a thick base is beside it, because the facts do not say; facts without the profile cannot tell a post that steps up right at the ring face from one that tapers (run `fusion_bearings` again for the profile).
- **A length measured to 0.02 mm failed a limit by 0.01.** `screws.under_head` failed "4.99 mm of plastic under its head ... under 5" on screws drawn with 5.0 (8 rows on one part): `fusion_screws` finds the end of a run of material along a line to 0.02 mm and stores it to 0.01. `screws.under_head`, `screws.engagement` and `screws.tip_bottoming` now compare such a length with its limit on the 0.05 mm measurement grid (`_gridded` in `gate_judges.py`, half up: 4.99 is 5.0, 4.97 is 4.95, a real 4.9 stays 4.9); the finding keeps the measured value. Lengths measured more finely are compared as they are: walls by rays (0.005), bearing travel (0.002), widths from the geometry. Tests that held "4.99 under 5 is a FAIL" were that defect and now hold the grid.

### Changed

- `skills/fusion-modeling/references/api-pitfalls.md`: a script 6 to 10 times slower than the same script a minute later is App Nap; what to do about it.
- The rule statements of `bearings.seat`, `screws.under_head`, `screws.engagement` and `screws.tip_bottoming` say the above; the rule blocks of `skills/print-design/SKILL.md` are rendered again. No threshold changed.
- Judges changed (`bearings_seat`, `inserts_relief_missing`, `inserts_wall`, `inserts_wall_at_relief`, `inserts_floor`, `screws_under_head`, `screws_engagement`, `screws_tip_bottoming`) and the hole classifier (`classify_stack` in `probes/_geom.py`), which every judge that reads a hole's class uses (`inserts_pocket_shape`, `inserts_mouth_reachable`, the `access` rules, `census_unknown_hole`, `inserts_no_tapped_plastic`, `fits_bought_pocket`). `gate.py status` lists a part as judged by an older judge only for a change inside `gate_judges.py`: a change of the classifier alone does not show there. After `--update-tools`, judge every part's stored facts again: `gate.py run --all --offline`.
- `_gridded` lowers each of the three screw limits by 0.025 mm in effect; with `screws.tip_room_min` set to 0 a tip measured 0.02 past the end of its hole passes.

### Privacy and side effects

- The activity is a request to the operating system that lasts as long as one call and is ended by it; it changes no setting of Fusion or of the system, and it does not keep the computer awake (idle system sleep stays allowed). Nothing else is new.

### Requirements

- None new. The activity needs `ctypes` and `libobjc`, which every macOS has; elsewhere nothing is done.

### Tested

- In a real Fusion on macOS (Apple silicon, on AC power): the slow state after 200 s of idle measured twice without the activity, and its absence after 200 s of idle with it. The full `gate.py run --part <the toothed part>` with 10 s pauses between its calls: 15 calls in under 6 minutes (census 1 call, screws 2, bearings 2, thin 6), no call over its budget, and the facts of all eight units byte for byte those of the slow 0.2.2 run.
- Without Fusion: the activity begins before the probe and ends after it, also when the probe raises; a failing `libobjc` gives a note and the unit's facts; off macOS no note. On macOS the real activity begins and ends inside the test process. Removed on a scratch copy, the tests failed.
- The three judge fixes: judge fixtures from the incidents and their twins that must stay rows (countersink with nothing or an insert on the axis, a 60 degree cone, an edge chamfer; a straight post 0.35 over, a post past the band, a taper turned round, an open entry, a bore loose at its stop; 4.9 and 4.97 under 5). Each guard removed on a scratch copy failed its tests. On the stored facts of a first audit, judged again offline, the false rows of the three kinds are gone and the real wrong pockets of the same part stay.
- Not tested: Windows (nothing changes there), Fusion with its window in front (App Nap does not apply).

## 0.2.2

### Fixed

- **`screws.shell_bears` failed a bearing clamp whose ring sits beside the gap.** Two parts screwed together with clamp room between their faces close on a bearing ring: one part holds the ring in a seat, flush with its own face, on a shoulder; a lip of the other part reaches across the gap and presses the ring's face. `fusion_screws` looked for a bought body only inside the slab of the gap at the screw, found nothing there (the ring lies wholly beside it), and every screw of the part was a `FAIL` "Nothing bought stands in the gap" on a correct design. Now:
  - a bought body is also looked for within 3 mm beside the gap along the screw on either side (the probe's existing look past a body's end, `_SCR_STACK_LOOK`), round the screw axis within `screws.bridge_search_r` as before. A body met there and not across the middle of the gap is taken only when it is a bearing (a ring of the bearing table, or named so, as for a clamp washer) and is marked `"beside": true`. Any other body met there (a seal cord in a groove that a tongue reaches across the gap to, a block in a pocket) is not measured and is named in `"passed_over"`: the gap stays a `FAIL`, as in 0.2.1, and its text names that body;
  - the lines along the screw through a body stand on every circle from the first one that meets it out to the search radius, and on at least three circles (0.2.1: the first three only), so a shoulder under one part of a ring and a lip over another part of it are both met, and a chamfer at the rim of a body first met on the last circle is still passed by the next one;
  - the same exact test decides: on one line parallel to the screw both parts press on the body, each within `bearings.contact_gap_max`. It now also checks that the load crosses the gap on that line: between the gap and the place where a part meets the body there is that part, the body or the measured contact air. A lip that overhangs air on every line within the search radius, and a ring that one shell holds in a closed pocket above or below its own hollow, read None on that side. The check reads only the stretch between the gap and the contact: a body that itself spans the heights of the gap on the line is read as in 0.2.1;
  - a bought body on which Fusion raises during these point tests no longer ends the unit: its row has `"unreadable"` (the error) and `"along": [null, null]`, and the gap stays a `FAIL` that says so.
- The rows say where to look. The `INFO` row for a bearing beside the gap says it does not stand in the gap but beside it, with both distances along the screw and its distance from the screw axis; its `FAIL` row says it "sits beside that gap". Rows for a body in the gap keep their text and their ids.
- `fusion_screws` returns a `"timing"` block beside its facts for each call: questions and seconds per stage (`screw`, `gap`, `washer`), the five bodies asked longest, and the stage a call stopped in.
- Thresholds and parameters are untouched. A ring that one part presses and the other stands off, a ring that only sits in a bore of one part, a pin through both parts and a board on the floor of a hollow shell stay `FAIL`, and every fixture of 0.2.1 keeps its verdict. Facts stored by 0.2.1 are judged as before until the screw unit of that part runs again.
- **`fusion_census` ran 73 s in one call on a toothed pulley** (6795 faces: 84 teeth on its rim, a bore with a bearing seat, six screw holes; budget 20 s). Measured on the fake API with a pulley of 84 teeth of 30 edges, a flange beside the teeth, and two neighbours of 2000 and 3000 faces, counting API reads per stage:
  - the cost was a planar face whose inner loop is the toothed outline (on the fake, the flange face round the teeth: 2520 edges, 11 340 points). `opening_shape()` tried the direction of every point against every point: about 200 million products of pure Python (8.3 s on the measuring machine, more inside Fusion), after 42 870 API reads of the edge walk, all in one piece with no clock. 0.2.1 made it worse: a call cut inside the point tests of that face started the face again from its first edge in the next call, so every later call paid the walk and the size again before its first stored answer;
  - the walk over the neighbours' faces (18 221 reads) was one piece too, and it ran again in every call that measured a stack. Each of those calls wrote `coaxial_skipped` again, so a unit of several calls listed those names two or more times: its facts were not the facts of one call;
  - one cylindrical face, one stack and one planar face were steps of any size between two looks at the clock.
- Now every stage reads the clock before each step, and a call that stops leaves its step in the cursor as plain JSON (no step is cut and started again). A step is one face of the part (one edge of a face or a loop with more than 32 edges), one occurrence of the walk over the document, one face of a neighbour, one stack, one planar face, one loop; the point tests inside a step go through the tape as in 0.2.1. A large loop is read edge by edge in up to three passes and sized by the streaming form of `opening_shape()` in `_geom.py` (`hull_keep`, `rect_direction`, `shape_of_rect`): each direction is tried only against the points that can be the largest or the smallest along some direction (a point at least 1e-6 mm inside the hull never is), first against the few dozen of them round the extremes of the direction before (their rectangle is never larger than that of all points, so a direction it does not make better is not better), and the answer is the same floats (760 random and degenerate outlines in the tests, the 84-tooth outline and a round one). On a convex or finely curved outline nearly every point is kept: those points stay in the memory of the Fusion process between two calls, not in the cursor (in the JOB line of every call and in the plan), and when that memory is gone (Fusion restarted, or the host sent a cursor again after an answer it never got) the loop is read again from its first edge; the third such call in a row ends the unit in an error. On the fake, the 84-tooth flange face takes 0.2 s of Python instead of 8.3 s, and a round outline of 11 340 points 0.6 to 0.9 s instead of 8 to 11 s (Python 3.14 / 3.9), and the largest cursor is 3.8 KB (a round outline kept in the cursor reached 469 KB).
- The neighbours are read once per unit. Only the cylinders whose axis can lie on the axis of a stack are kept (a necessary test with 0.01 mm to spare; `coaxial_cylinders()` decides as before), and a face's box is read only for those; a neighbour's place relative to the part is read again when its faces are read and kept in the cursor as a digest only. When a neighbour that was already read changed between two calls (revision id, face count, volume, area; its place relative to the part, which also changes when the part moves), the walk over the neighbours starts again with a note; the third such restart in a row ends the unit in an error, and a call that goes on with what the call before read ends the row. A neighbour for which Fusion gives no state is compared by its face count and box; with nothing readable at all, a note in every call says that its faces are joined over the calls unchecked. `coaxial_skipped` is written once per unit.
- Costs removed with the same facts: the openings stage no longer reads every face of the part again (the first walk notes the planar faces with an inner loop), and a property is read once where 0.2.0 read it up to six times (a cylinder's axis, an edge's curve, its evaluator). On the fake at full size, one call reads 146 500 API values instead of 180 300 (138 800 instead of 167 600 in the stages `faces`, `neighbours`, `stacks` and `openings`, which the probe counts itself; `prelude` and `state` are counted only in seconds); the point tests are the same 39 505.
- `fusion_census` returns a `"timing"` block beside its facts, summed over the calls of the unit, and one line of it in the notes: seconds, API reads and `pointContainment` calls for `prelude`, `faces`, `neighbours`, `state` (reading the neighbours' state), `stacks`, `openings` and `shape` (the Python that sizes a large loop), the five slowest faces of the part (`{"face", "s", "api", "pc"}`), `gap_s`, the longest stretch of any call without a look at the clock (`call_gap_s`: this call; it includes the start of the call, the signature and the first step), and `cursor_bytes`, the size of the cursor the call returned. `_fusion.py` notes every read of the clock (`gate_left`) for that (`gate_clock_gap()`).
- The census cursor has a version (`"v": 2`). A census unit that a 0.2.0 or 0.2.1 run left half done goes on where it stopped, with a note ("a cursor of the census before 0.2.2 was taken over at ..."): in the faces, at the stack it reached (with a new walk over the neighbours; `coaxial_skipped` is not written again when a stack was measured, though the older calls may have written it more than once), or at the planar face it reached (its tape is dropped); the planar faces those versions did not note are looked at again in the openings stage. A cursor of a later version ends the unit in an error. `args.tape_limit` now counts every point test of the unit and `args.limit` every step (tests, slow machines).
- Tested without Fusion (`tests/fake_toothed.py`, a fake that counts every API read): the facts of one call are byte for byte those of 0.2.1 on that pulley, on a small toothed one, on a round one and on the demo part, and equal the facts over many calls with one step a call, one point test a call and mixed limits; with one step a call no call reads more than 80 API values of any stage; the `"timing"` counts equal the fake's counts per stage; under a cost model of 0.15 ms an API read and 1 ms a point test with a budget of 20 s (Python time not in the model: it is measured apart, above), 0.2.1 runs one call 21.7 s and this version ends every call at 17.5 s with no stretch over 0.06 s without the clock; a neighbour edited or moved while its faces are read starts the walk again with one note and the facts of one call on the changed scene; three restarts far apart are no error, a restart before every call is; a cursor of 0.2.1 at each of its three places goes on to the facts of one call; the outline memory lost or overtaken mid-loop reads the loop again with the same facts. Each of these guards was removed on a scratch copy and its test failed. Not run in Fusion yet.

### Tested

- Fake API: a ring in a seat of the head-side part and of the thread-side part (`INFO`, the text says "beside" with both numbers), the same with the lip 0.3 short (`FAIL`, "stands 0.3 mm off"), a ring 9 mm from the axis, a ring named as a washer and the same ring under a name that says nothing (not taken; the last one named in `"passed_over"`), a seal cord and an O-ring in a groove that a tongue reaches across the gap to (`FAIL`, passed over), a lip that overhangs air on every line (`FAIL`), a chamfer at the rim of a ring first met on the last circle (`INFO`, as in 0.2.1), a body whose point tests raise (`FAIL`, the unit goes on), a ring and a block in a closed pocket above the hollow of one shell (`FAIL`), and the cursor stopping inside the gap search of a screw (facts equal to one call). Each new guard was removed on a scratch copy and its test failed. Four judge fixtures from these scenes.
- In a real Fusion: on a toothed part of about 6800 faces bolted to a tube by 6 screws across a 1.0 mm clamp room, with the outer ring of a large thin-section bearing seated in the toothed part and pressed by a lip of the tube, the 6 `screws.shell_bears` FAIL rows became one `INFO` (`along` [0.0, 0.0], `beside` true). On the case that must stay a defect (a ring one part presses while the other stands off) it is one grouped `FAIL`: "... presses on it, ... stands 1 mm off it along the screw".
- In a real Fusion, `fusion_census` on the same toothed part: 2 calls, the longest stretch without a clock read 0.36 s (0.2.1: one call of 61 to 73 s, adopted late). The late answer of 0.2.1 was adopted live as designed.
- Known: `fusion_thin` on that part works up to 1.6 s past its 20 s budget (reported as `OVER BUDGET`, well inside the HTTP timeout).

## 0.2.1

### Fixed

- **A unit that answers late is no longer lost, and a run can no longer get stuck on it.** When a Fusion call of `gate.py run`, `scan` or `export` got no answer within its time (budget + 10 s) and Fusion was still busy, the gate stopped with exit 4, as it should. The next call then deleted the result file the unit had written meanwhile and sent the same unit again from its start, so a unit that needed longer than one call could never be passed and nothing behind it ran. Now:
  - before a call is sent it is noted twice: `gate/run/pending.json` names it for every gate command (unit, run, signature, cursor, send time; no absolute path), and the unit keeps its own note in its plan (`"flight"`). The first is removed as soon as any gate command sees that the call is over; the second only when this unit's answer was processed. So a `gate.py scan` or `gate.py export` between the timeout and the next `gate.py run` does not make the run forget which call it waits for;
  - the next call of the unit first asks what became of that call. A result file written by exactly that call (same run, unit, probe, part and signature; the cursor it was sent with, by a digest the unit takes before its probe runs; not older than the call; no error) is taken as the unit's answer, marked late, and the unit goes on from its cursor. A result file is removed only after it was read and refused. A result file that holds an error is that call's answer too: the error is counted as a failed call and its text is kept (`UNIT ERROR` line of the run, the plan, the facts file);
  - a late answer of `fusion_scan` is taken only while its file is younger than 10 minutes. A scan carries no signature (it reads them), so nothing else ties an old scan to the model as it is now: an older file is not used and the scan runs again;
  - while that unit has written no result and Fusion runs no ping script, the call exits 4 again and sends nothing. A second copy of a unit is never put behind the first (0.2.0 did that on every repeated call). The same holds when the connection breaks while a unit works: one ping, and no answer ends the call like a timeout. The message says whether something listens at the endpoint (Fusion is busy or hangs) or nothing does (Fusion is not running);
  - a unit that got no answer and left no result twice in a row is given up, after its result file was looked for: its rules are `NOT_RUN` with the text "over budget, not run", the run goes on with the next unit and exits 1 at the end. It is never skipped silently. With `--audit` the part of such a unit is not taken into the baseline of inherited parts (nothing judged the rules of that unit), so the audit exits 1 for it and `run --changed` still lists the part.
- **Calls over their budget are reported.** Every call that worked longer than its unit's budget, and every call that got no answer and left no result (also when the next call of the unit answered), is one `OVER BUDGET` line in the output of `gate.py run` and `gate.py scan`, in `gate.py status`, in `gate.py doctor` and in the report (`"over_budget"`, and a section of the `.md`); the facts file of the unit holds the same line as a note. It is a defect of a probe to report, not a finding of a part. `status` and `doctor` also say when a call was sent and its answer was not read yet.
- **`fusion_bearings` keeps its calls inside the budget.** One bearing was one step, whatever it cost: a large ring against a neighbour with thousands of faces can take several times the budget in one call. The work on one bearing is now cut into steps (one neighbour at one side of the bearing, one neighbour as a seat, the clamp of one ring), the clock is read between the steps and every 16 point tests inside a step, and a call that runs out of time returns a cursor that carries the finished steps and the answers of the step in progress as plain JSON. The facts of a bearing measured over several calls are the facts of one call. A step whose questions differ from the ones its stored answers belong to is measured again; three such calls in a row end the unit in an error (its rules are `NOT_RUN`). The walk over the faces of a clamp body with more than 500 faces, which reads no clock, does not start late in a call.
  - Fewer point tests, with the same facts: a neighbour whose bounding box does not reach the slab in front of a ring face, and a body whose box cannot hold the wall of a seat, are not asked; "does it touch the other ring" reads the answers of the other ring's own test instead of asking the same points again; a bearing that another printed part holds is named in `near_bearings` as soon as that is certain, without measuring the rest.
  - The envelope of the unit has a `"timing"` block beside the facts (seconds and `pointContainment` calls for faces, seats and travel, the seconds before the first step, in the face walks of clamp bodies and in reading the state of the neighbours, the five slowest bodies), and one line of it in its notes. No judge reads it.
- **A feature measured over several calls is measured on one state of the model.** The cursor of a bearing, screw, washer, pocket or opening in progress carries the bodies it was measured against: place, name and box of every body it could meet, and the state (revision id, face count, volume, area) of every body that answered a point test or was cut. When a neighbour moved, grew, was hidden or changed inside its box between two calls, that feature is measured again from its start, with a note; the third such call in a row ends the unit in an error. For a bearing only the bodies the bearing uses count. When the list of bearings near the part differs from the one the cursor counts in, the unit ends in an error instead of measuring another bearing under the old index.
- `fusion_gap` does not start a distance measurement with less time left than 1.5 times the slowest one of the call (was: a fixed 2.5 s).
- **`fusion_inserts`, `fusion_screws`, `fusion_openings` and `fusion_census` had the same defect** (one pocket, one screw, one feature, one planar face with many openings was one step) and go on inside such a step now, the same way. `fusion_screws` no longer writes a `"late"` row for a body it had no time for: the screw goes on in the next call. `fusion_openings` writes a feature into the facts when all its paths are done. `fusion_census` reads the clock every 4 cylinder faces (was 16) and before the walk over the neighbours' faces.
- `fusion_thin`: the sign of a turn (one point test per edge) is asked for the twelve sides that are listed, not for every edge of the face. A face with a toothed outline cost thousands of point tests for twelve rows.

### Changed

- The cursor of `fusion_bearings`, and the cursor of `fusion_inserts`, `fusion_screws`, `fusion_openings` and `fusion_census` when a call stops inside a step, is an object. A plain number from a plan made by 0.2.0 is still read. Finish a run that is in progress before `--update-tools`, or start it again after.
- The NOT_RUN row of a unit that was given up says why ("no facts from fusion_bearings: over budget, not run: ...") instead of the general text.
- The facts file no longer keeps which call wrote a unit's result (`"asked"`, `"late"`): the host needs it only to adopt a late answer.
- Stamps, judges and thresholds are untouched: no check passes that failed before.

### Tested

- In a real Fusion, on the three parts round a large thin-section bearing whose bearing unit ran 55 to 65 s in one call under 0.2.0 (a neighbour with about 6800 faces seats its outer ring): every call 17 to 21 s, no restart, no unit error; the bearing facts measured over 5 calls are equal to the facts of the single long call of 0.2.0. One `fusion_census` call on the 6800-face part worked 21.1 s of 20 s and was reported as `OVER BUDGET`.
- Not tested live: a late answer after a real timeout, and the give-up after two silent calls (fake API only).

## 0.2.0

### New

- **The print gate** (`gate` skill, `tools/gate/`, `rules/`). Design rules live in a registry, each with the incident behind it, a number a project can set and, where possible, a check. `gate.py run` checks the parts that changed in Fusion, `gate.py export` writes a checked STL with a stamp tied to the geometry, `gate.py verify` holds a `.3mf` against its stamps and its declared fit settings, and the `.3mf` writer asks the gate at every save. Opt-in per project: `setup_project.py <dir> --gate`. A project starts in `observe` mode, where the `.3mf` writer refuses nothing and only reports (`gate.py export` still writes no STL for a part with open findings).
- **The print ledger** (`print-ledger` skill, `tools/ledger/`). It records what the tools wrote into each `.3mf` and what the owner said is printed, and answers whether a part may still change. It is off until `ledger.py init` or `setup_project.py --gate`. In a 0.1.x project the tool arrives only with `--update-tools`, which also brings the changed `insert_pockets.py` and `bambu3mf.py` (see the next heading).
- **A SessionStart hook** (`hooks/hooks.json`), the first hook this plugin ships. At every session start (also resume, clear and compact), in every project where the plugin is enabled, Claude Code runs `python3 <plugin>/tools/gate/gate_hook.py session-start`. It looks for `gate/project.json` in the session folder and above it. In a gated project it prints a short gate digest into the session (a title line plus at most 15 lines, and one more line when the vendored copy is forked or older than the plugin); in any other folder it prints nothing and exits 0 within about a second. `python3` is started directly, with no shell.
- **Adopting a project that already has STL files**: `"stl_names"` per document in `gate/layout.json` maps a part to the file name its STL already has, so `gate.py export` writes the file the build table reads. The map takes effect with the next `gate.py scan`, which says which parts got another file. A mapped file that is another part's, and any file that two parts name as their STL, is a `NAME CLASH` line of the scan (exit 4); the part stays in the census with the file it had.
- `setup_project.py --gate` and `--update-tools` leave a gate alone that they cannot prove to be the plugin's: a vendored copy that was changed by hand is listed as `FORKED` (also when the changed file is `meshid.py` or a ledger tool in `bambulab/tools/`, which is then kept as it is), and a `gate/tools` or `gate/rules` folder without `gate/LOCK.json` whose files differ from the plugin's is listed as `NO LOCK`. Nothing is copied or deleted under `gate/tools` and `gate/rules` until `--overwrite-fork` is given, and while a gate is forked `meshid.py` and the ledger tools in `bambulab/tools/` stay at the version of its lock (the `FORKED` line says so). When `--gate` finds such a folder and no `gate/tools/gate.py`, it writes nothing at all: no `gate/project.json`, no ledger, no `.gitignore` line. A release that changes `meshid.py` or a ledger tool refreshes a gated project without calling it a fork.

### Changed for every project after --update-tools (with or without a gate)

- **Skills and tools can disagree until you refresh.** After the update `print-design` describes the 0.2.0 insert checks (every wall under 4.0 is a row, a melt relief only where 2.45 mm stays beside it) while a 0.1.x `fusion/tools/insert_pockets.py` in a project still passes 3.8 and cuts reliefs where 1.2 stays. Refresh every project. When `fusion/tools/VERSION` is older than the plugin, refresh before trusting a `wall()` or `relief()` result.
- **`insert_pockets.wall()`**: the pass line is now the wall target, 4.0 mm (was 3.8). Every pocket under the target is a row, so a wall of 3.8 up to 4.0, the old D12 boss round the D4.4 lead-in, passed in 0.1.5 and is now a `GROW` or `BLOCKED` row. The constant `WALL_PASS` is removed: a script that reads `g['WALL_PASS']` gets a KeyError. For the old listing call `wall(part, target=3.8)`. For the gate, the owner sets `inserts.wall_target` in `gate/owner/params.json` (`gate.py owner param ...`).
- **`insert_pockets.relief()`**: the plastic kept beside a melt relief is 2.45 mm (was 1.2). It is a decided value, not a measured one. Pockets that already have a relief are left alone and are still recognised by `audit()`. A pocket without one may now get a smaller fallback (a D12 boss gets D6.5 or D5.9 instead of D7.5) or a `skipped` row (a D10 boss got D7.5, now "shave by hand"). The gate lists each relief with 1.2 to 2.45 mm beside it until the owner sets the value.
  - To keep the old relief wall by hand: set `g['PARAMS'] = {'inserts.relief_wall_min': 1.2}` (or `RELIEF_WALL = 1.2`) before calling `relief()`.
  - To keep it in a gated project: `python3 gate/tools/gate.py owner param inserts.relief_wall_min 1.2 --tag U --source "..." --quote "..."`.
  - Always run `relief(part, dry=True)` first and read `relief_d` and `skipped` before cutting.
- **The gate does not hand a project's numbers to `insert_pockets.py`.** It measures with its own probe and does not load that file. A hand-run `wall()` or `relief()` uses the plugin's constants unless you set `g['PARAMS']` yourself from the values in `gate/owner/params.json`; in a gated project the gate's row is the verdict.
- **`bambu3mf.update_meshes()`** prints two new kinds of lines: `no object in the project for N STL(s): ...` and `on no plate: <object>`. The same lists are in `bambu3mf.LAST_UPDATE`.
- **`--update-tools` adds four files** to `bambulab/tools/`: `ledger.py`, `ledger_cache.py`, `ledger_mesh.py` and `meshid.py`. The print ledger does nothing until `bambulab/ledger/` exists (`ledger.py init`), and its slicer-cache reader stays off until you switch it on.
- The SessionStart hook runs in these projects too. It prints nothing in a project without `gate/project.json`.

### Privacy and side effects

- The gate and the print ledger are opt-in per project. Without `gate/project.json` or `bambulab/ledger/` above a `.3mf`, the guard, the stamps and the checks do not run and a save records nothing. `setup_project.py --gate` turns both on; `ledger.py init` turns the ledger on alone.
- In such a project every `.3mf` save appends to the committed `bambulab/ledger/events.jsonl`: object name, plate, relative STL path, mesh hashes and the writing tool. Copies of printed STLs go to the committed `bambulab/ledger/printed/`.
- The owner's sentences are stored word for word (`--quote`) in committed files: `bambulab/ledger/events.jsonl` and `gate/owner/*.json`. If your project repository is public, these become public with your next push.
- Optional, macOS only, off by default: a reader of Bambu Studio's URL cache database (`~/Library/Caches/com.bambulab.bambu-studio/Cache.db`). It runs only after `ledger.py cache on --quote "<your yes>"` on that machine; `ledger.py cache status` prints exactly what it reads. It copies the database to a private temporary folder, reads the copy and deletes it. That database holds signed download links of every job the slicer showed, other projects included. The reader keeps four things per job: upload time, plate index, the plate picture and its MD5, in the git-ignored `bambulab/ledger/local/`. It never stores, prints or opens a link, and refuses to run when the switch or that folder is committed. A job enters the committed journal only when you tie it to an object of the project.
- The ledger has no network code and connects to no printer or cloud account.
- The hook reads only local files, calls neither Fusion nor the network, and runs the plugin's own code only, never the copy inside a project. It writes nothing, except in a gated project the git-ignored cache of STL hashes (`gate/run/stl_hashes.json`). It goes away only with the plugin (or with all hooks, via Claude Code's `disableAllHooks` setting).
- The gate sends its units (project paths, document and part names, parameters, the probe source) to Fusion on this machine only. `"fusion"."endpoint"` in `gate/layout.json` must be `http://` on `127.0.0.1`, `localhost` or `::1`; anything else stops `scan`, `run` and `export` with a message, and `doctor` says it. Neither the gate nor `fusion_mcp.py` sends a request through `http_proxy`. `FUSION_MCP_URL` of `fusion_mcp.py` stays your own choice.
- The committed facts files (`gate/facts/`) hold neither the names of other documents open in Fusion nor absolute paths. Facts written by a gate from before this release still hold both: run those parts again, or strip `open_documents` and the absolute `stl` values, before making such a repository public. `--gate` now also git-ignores `gate/reports/guard.log`, which can hold absolute paths.
- In a gated project every `.3mf` save costs time, also a settings-only one and also in `observe` mode: the meshes of the archive and of the file on disk are parsed and hashed in pure Python. Count on about 5 to 15 s per million triangles per save (measured: 0.77 million triangles, 0.7 s without the gate, 4 to 12 s with it on Python 3.9); round parts placed in the same run can take several times longer. Nothing fails; a tool that saves more than once pays it each time.

### Requirements

- Claude Code 2.1.139 or newer: the hook uses the exec form (`command` plus `args`), which that version added.
- Python 3.9 or newer as `python3` on your PATH. The hook and every tool call it by that name.
- Unchanged: Autodesk Fusion with its MCP server on, and Bambu Studio for the print side. The core tools still use only the Python standard library.

### Not tested

- Windows. The paths exist, but nothing was run on a real Fusion or Bambu Studio there. Without `python3` on PATH the hook fails at each session start; that does not block the session.
- The gate's Fusion units (the scan, the eight design units of a part and the export unit) ran in a real Fusion on one machine against one assembly of about 100 occurrences with one linked document. The whole `gate.py export` command, `enforce` mode, and a scan after the document was closed and opened again ran only against the fake API of the tests.
- The 2.45 mm beside a melt relief is computed and decided, not measured on a print.
- The save cost above was measured on one machine.
- The slicer-cache reader exists for macOS only.
