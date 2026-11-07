---
name: fusion-cam
description: CNC milling from Autodesk Fusion Manufacture through the API - stock and WCS that match the real board, hole/bore/face operation parameters, safe heights, posting with grbl.cps, and checking the posted G-code. Use when creating or changing Fusion CAM setups, toolpaths or NC programs, posting G-code for a GRBL router, or when a cut came out deeper or shallower than expected.
---

# Fusion CAM to a GRBL router

Claude does the CAM in Fusion; a person runs the machine. Never press Start, jog, or switch the spindle on remotely.

## Rules

1. **Never edit G-code, by hand or by script.** Every change goes through Fusion Manufacture and a re-post. A patched `.nc` disagrees with the document, and the next re-post silently drops the patch.
2. **The model is the real part.** Stock offset top is 0, and a hole depth in the model is the depth you want cut. A fake "+1 mm stock" shifts every depth by 1 mm, and the operator measures the result against the model.
3. **Nothing stays "out of date".** After any model or CAM change, in one script: `design.computeAll()`, regenerate all toolpaths, post **every** NC program, then `doc.save()`. A model edit makes every dependent operation stale, including ones in programs you did not touch.
4. **Read the depths from the posted file before you state them.** Report min Z and max Z per program from the `.nc`, never from the parameters you set.

## Stock and WCS

- Put the WCS where the operator can touch it off, for example the top back-left corner of the board, and write that point in the job steps.
- `job_stockOffsetTop` = 0 and side offsets = 0 when the board is cut to size.
- A bolted-down board that is faced later: model it at its real thickness. Face below the model top with a negative stock to leave (see below), not with extra stock.

## Operations

Set parameters with `op.parameters.itemByName(name).expression = '5 mm'`. A choice parameter is read as `.value.value`.

### Holes and counterbores: bore milling, not pocket cycles

`circular-pocket-milling` plunges straight 2 mm per level and then moves in XY. For smooth helical entries use `cycleType = 'bore-milling'`:

| Parameter | Use |
|---|---|
| `pitch` | 0.5 mm per turn in MDF with a 3.175 mm flat end mill |
| `useMultipleSteps`, `numberOfSteps`, `cycleStepover` | For a hole much wider than the tool. The stepover has to fit (14 mm with a 3.175 mm tool: 3 steps × 1.5 mm), otherwise "stepover too large" |
| `holeMode` | `'diameter'` with a min/max range picks holes by size from the model. Valid values: `hole-faces`, `selection-faces`, `entire-hole`, `selection-points`, `diameter`, `unmachined`, `hole-signature` |
| `tool_feedPlunge`, `tool_feedRamp` | Slow, about 150 mm/min |

Heights for a counterbore with a through hole below:

- Counterbore: top `'from hole top'` 0, bottom `'from stock top'` −depth. A `'from point'` bottom loses its reference when the model changes ("top height must be above bottom").
- Through hole: top `'from hole top'` 0, which is the counterbore floor, so it starts in air and does not plunge from the surface. Bottom `'from hole bottom'` −1 mm, so it breaks through into the board below.

To remove holes, delete them from the sketch and re-pick the extrude profiles. Do not exclude them in CAM: the drawing and the model would still show them.

### Facing

A face operation cuts to the flat areas of the model, so with a real-thickness model it cuts nothing. To skim a bolted board, set `useStockToLeave` = true, `stockToLeave` = 0, and `verticalStockToLeave` = −1 mm (negative is below the model top), with bottom `'from surface bottom'` 0. Check the coolant: the default Flood emits M8.

## Safe heights and the post

`grbl.cps` "Safe Retracts" (`safePositionMethod`) defaults to `G28`. That emits `G28 G91 Z0`, which on a homed GRBL machine drives Z into the limit switch. The `G53` choice **still emits `G28 G91 Z0`** for Z. Only `clearanceHeight` is safe:

```python
for p in cam.ncPrograms:
    pp = p.postParameters
    pp.itemByName('safePositionMethod').value.value = 'clearanceHeight'
    p.updatePostParameters(pp)
```

- For a high travel between holes, set `retractHeight` `'from stock top'` 30 mm and `clearanceHeight` `'from retract height'` 0. A "go near machine Z0" move is not possible from Fusion with this post without G28.
- With `clearanceHeight`, the program starts with an XY rapid at the **current** Z and only then goes to the retract height. The job steps must tell the operator to raise Z 30–40 mm after Z0 and before Start.
- Before you hand a file over: `grep -c 'G28\|G53'` is 0, and min and max Z are what the steps say.

## API

| Task | Call |
|---|---|
| Regenerate | `cam.generateAllToolpaths(False)`, then loop `while not fut.isGenerationCompleted: adsk.doEvents()` |
| Post | `prog.postProcess(adsk.cam.NCProgramPostProcessOptions.create())` |
| Model edit first | `design.computeAll()` before regenerating |
| Save | `doc.save('what changed')` after posting |

- Scripts fail while a command dialog is open in Fusion (Simulate, an operation editor). Ask the operator to close it. Read-only scripts still work.
- Personal licences post one tool per file and may turn rapids into feed moves. Check the first post.

## Operator page

Keep a per-job step list next to the `.nc`: zero point, clamps away from the toolpath, the Z-raise step before Start, the tool, and the depths read from the file. Rewrite it whenever a re-post changes depths, hole count, or heights.
