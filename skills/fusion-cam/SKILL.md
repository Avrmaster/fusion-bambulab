---
name: fusion-cam
description: CNC milling from Autodesk Fusion Manufacture through the API - stock and WCS that match the real part, hole/bore/face operation parameters, safe heights, posting with grbl.cps, checking the posted G-code, and finding why a cut came out at the wrong depth. Use when creating or changing Fusion CAM setups, toolpaths or NC programs, posting G-code for a GRBL router, reusing an old CNC job, or when a cut came out deeper or shallower than expected.
---

# Fusion CAM to a GRBL router

Claude does the CAM in Fusion; a person runs the machine. Never press Start, jog, or switch the spindle on remotely. The machine-side station (operator page, laptop sync) is the `cnc-station` skill.

## Rules

1. **Never edit G-code, by hand or by script.** Every change goes through Fusion Manufacture and a re-post. A patched `.nc` disagrees with the document, and the next re-post silently drops the patch.
2. **The model is the real part.** Stock offset top is 0, and a depth in the model is the depth you want cut. A fake "+1 mm stock" shifts every depth by 1 mm, and the operator measures the result against the model.
3. **Nothing stays "out of date".** After any model or CAM change, run `cam_refresh.py`. A model edit makes every dependent operation stale, including ones in programs you did not touch, so it regenerates and posts all of them.
4. **Read the depths from the posted file before you state them.** Run `nc_check.py` and quote its numbers, never the parameters you set.

## Tools

Both are vendored into the project by `setup-project`.

`fusion/tools/cam_refresh.py` runs inside Fusion. Pass this as the `fusion_mcp_execute` script:

```python
P = r'<project>/fusion/tools/cam_refresh.py'
g = {'__file__': P}; exec(open(P, encoding='utf-8').read(), g); run = g['run']   # or g['status'] with readOnly: true
```

- `status` lists every operation (ok, OUT OF DATE, error) and every NC program with its Safe Retracts and output file.
- `run` does four things in turn. Call it again until it prints `DONE`; each call returns within 40 s.
  1. Recomputes the model and regenerates what is invalid.
  2. Posts every program with Safe Retracts = `clearanceHeight`.
  3. Runs `nc_check` on each posted file.
  4. Saves the document.

  An error in the G-code stops it before the save.

`cnc/nc_check.py` (or `${CLAUDE_PLUGIN_ROOT}/tools/cnc/nc_check.py`) reads `.nc` files and changes nothing:

```bash
python3 cnc/nc_check.py job.nc --x 0:400 --y=-300:0 --z=-11:
```

- It prints the cutting box and overall box (arcs included), first Z, tools, speeds, feeds and a rough time.
- It exits 1 on G28/G30/G53/G92 (unless `--allow`), on a rapid sideways below Z0, and on a cut outside the given ranges.
- It warns on a first move that goes sideways at an unknown Z, on coolant, and on a rapid down deeper than the file has cut.
- The time uses a 3000 mm/min rapid. Trust it over a guess: it often differs from the guess by 2×.

## Before a new job

- **Look for an old one first.** Old posts are in `~/Documents/Fusion 360/NC Programs/<job>/`, and their Fusion documents are in the user's projects. A job done before often only needs a new zero.
- **If the user says the old program worked, run it unchanged.** Copy the old `.nc` byte for byte. Re-posting from the document can change cycles, depths or the post version. Offer fixes as a question.
- **Run `nc_check` on an old file too.** A file from another post version can contain G28.

## Machine settings

Read GRBL's settings before the first job on a machine, with `$$`, `$#` and `$G`. With a station, use `cnc/grbl.sh` while no sender holds the port.

| Setting | Why it matters |
|---|---|
| `$22` homing | 1 = the machine homes. Machine Z0 is then at the top switch, and anything that goes to machine Z0 (`G28`, `G53 Z0`) drives into it |
| `$20` soft limits, `$130–132` travel | With soft limits on, a move past travel alarms instead of crashing. Travel is also the largest stock that fits |
| `$110–112` max rate | The rapid speed; use the X/Y value as `--rapid` for the time estimate |
| `$#` G28/G30 positions | Stored positions that `G28` and `G30` go to |
| `$G` modal state | Units, G90/G91 and the work offset left from the last job |

Write the machine's numbers in the project's notes, not in a skill.

## Stock and WCS

- Put the WCS where the operator can touch it off, such as a top corner of the stock. Write that point in the job steps.
- Stock offsets are 0 when the stock is cut to size.
- A board that is faced after it is bolted down is modelled at its real thickness. Face it below the model top with a negative stock to leave (see Facing below), not with extra stock.

## Operations

Set parameters with `op.parameters.itemByName(name).expression = '5 mm'`. A choice parameter is read as `.value.value`.

### Holes and counterbores: bore milling, not pocket cycles

`circular-pocket-milling` plunges straight down one step per level and then moves in XY. For a smooth helical entry use `cycleType = 'bore-milling'`:

| Parameter | Use |
|---|---|
| `pitch` | depth per turn; small (a fraction of the tool diameter) in wood and plastics |
| `useMultipleSteps`, `numberOfSteps`, `cycleStepover` | for a hole much wider than the tool. Steps × stepover must cover (hole − tool) / 2, and each step must stay under the tool radius, or Fusion says "stepover too large" |
| `holeMode` | `'diameter'` with a min/max range picks holes by size from the model. Other values: `hole-faces`, `selection-faces`, `entire-hole`, `selection-points`, `unmachined`, `hole-signature` |
| `tool_feedPlunge`, `tool_feedRamp` | slow; the ramp feed drives the helix |

Heights for a counterbore with a through hole below it:

- **Counterbore:** top `'from hole top'` 0, bottom `'from stock top'` minus the depth. A `'from point'` bottom loses its reference when the model changes, and Fusion then reports "top height must be above bottom".
- **Through hole:** top `'from hole top'` 0, which is the counterbore floor, so it starts in air instead of plunging from the surface. Bottom `'from hole bottom'` with a small negative offset, to break through into whatever is below.

To remove holes, delete them from the sketch and re-pick the extrude profiles. Do not exclude them in CAM: the model and the drawings would still show them.

### Facing

A face operation cuts down to the flat areas of the model, so on a real-thickness model it cuts nothing. To skim:

- `useStockToLeave` = true, `stockToLeave` = 0;
- `verticalStockToLeave` = minus the skim (negative means below the model top);
- bottom `'from surface bottom'` 0.

Check the coolant: the default Flood emits M8.

## Safe heights and the post

`grbl.cps` "Safe Retracts" (`safePositionMethod`) defaults to `G28`. That emits `G28 G91 Z0`, which on a homed GRBL machine drives Z into the limit switch. The `G53` choice **still emits `G28 G91 Z0`** for Z. Only `clearanceHeight` is safe. `cam_refresh.py` sets it; by hand:

```python
pp = prog.postParameters
pp.itemByName('safePositionMethod').value.value = 'clearanceHeight'
prog.updatePostParameters(pp)
```

- **High travel between holes:** `retractHeight` `'from stock top'` high enough to clear the clamps, and `clearanceHeight` `'from retract height'` 0. A move to "near machine Z0" is not possible from Fusion with this post without G28.
- **The first move is sideways.** With `clearanceHeight`, the program starts with an XY rapid at the **current** Z and only then goes to the retract height. The job steps must tell the operator to raise Z well above the clamps after setting Z0 and before Start.

## When a cut comes out at the wrong depth

Measure the actual depth, then account for every millimetre before you change anything. Add these up:

| Source | How to find it |
|---|---|
| Stock offset top | Setup > Stock. Any value above 0 moves every cut down by that much |
| Model depth | Measure the feature in the model, not the operation's height field |
| Bottom height offset | The operation's Heights tab: the offset and what it is measured from |
| Posted Z | min Z per operation from `nc_check` |
| Z0 touch-off | The paper method leaves 0.1 mm or more. A tool that slipped in its collet adds more |
| Stock surface | A warped board or a chip under it |

If the posted Z matches the model, the cause is on the machine side, so fix the steps, not the CAM. If it does not match, fix the model or the stock in Fusion and run `cam_refresh` again. Show the breakdown to the user before you change anything.

## API

| Task | Call |
|---|---|
| Regenerate | `cam.generateAllToolpaths(True)` (only invalid ones), then loop `while not fut.isGenerationCompleted: adsk.doEvents()` |
| Post | `prog.postProcess(adsk.cam.NCProgramPostProcessOptions.create())` |
| Model edit first | `design.computeAll()` before you regenerate |
| Find the CAM product | loop over `doc.products` for `productType == 'CAMProductType'`; `itemByProductType` raises when the document has none |

- Scripts fail while a command dialog is open in Fusion (Simulate, an operation editor). Ask the user to close it. Read-only scripts still work.
- A Personal licence posts one tool per file and may turn rapids into feed moves. Check the first post with `nc_check`.
