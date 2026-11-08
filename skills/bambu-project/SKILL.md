---
name: bambu-project
description: Create and update Bambu Studio .3mf projects from scripts. Use when building or changing a .3mf, adding parts or plates, setting print orientation, supports, filaments or slicer settings, swapping in new STLs, or when Bambu Studio will not open a project or prints something unexpected.
---

# Bambu Studio projects from code

Tools are in `bambulab/tools/`, and the project's `bambulab/build_3mf.py` is the source of truth. If they are missing, run the `setup-project` skill. Do not re-derive the .3mf format: [references/3mf-format.md](references/3mf-format.md) has it.

## The one hard rule

**Never write a .3mf while Bambu Studio has it open.** Its next save overwrites your edit. Wrap every edit:

```bash
python3 bambulab/tools/with_bambu_closed.py bambulab/Project.3mf python3 bambulab/build_3mf.py
```

An open Bambu is not a reason to stop or to ask first: run it. It asks Bambu to quit; with nothing unsaved Bambu closes on its own, the command runs and the project reopens. With unsaved changes Bambu shows its save prompt and the tool exits with code 3, having edited nothing. Then ask the user whether to keep those changes. They answer Bambu's prompt, Save or Don't Save. Run again with `--reopen`, which waits for Bambu to close and reopens the project afterwards. Never fall back to writing directly. If the user saved, the sync keeps their edits; check it did not undo yours.

Open one project at a time. A second `open` imports its parts into the first as geometry.

## Where this skill stops

This skill makes the project. It does not slice for printing or talk to printers. If the user wants that from Claude, use [bambu-printer-mcp](https://github.com/DMontgomery40/bambu-printer-mcp) when it is connected, and suggest it when it is not. Its `slice_stl` slices a project and its `print_3mf` sends a sliced file. To make a sliced file yourself: `BambuStudio --slice 0 --outputdir <abs-dir> --export-3mf sliced.3mf <project.3mf>`, on a copy. `slice_check.py` below only proves a project slices.

Starting a print, heating or sending to a printer needs the user's explicit request, every time, even when the tool would not ask.

## Building

Edit `PLATES` and `PROJECT` in `build_3mf.py`.

```python
PLATES = [
    # flat face on the bed, gussets up: no supports
    ('[TODO] frame + ring', [('Frame', 'frame.stl', '+z'), ('Ring', 'ring.stl', False)], {'cols': 2, 'gap': 6}),
    ('[TODO] bracket', [('Bracket', 'bracket.stl', '-x,z45', {'wall_loops': '6'})]),
]
```

- **Orientation** names the model face that goes on the bed: `'+x' '-x' '+y' '-y' '+z'`, or `False` for as modelled. Add `',z45'` to turn it on the bed. A tilted face takes its outward normal: `'n0/-0.994/0.113'`. Choose it deliberately and comment why, and never change a printed part's orientation unasked.
- **Layout:** `{'cols': n, 'gap': mm}`, `{'rows': [2, 3]}`, or `{'_placed': [(x, y, angle), …]}` for exact positions. `bambu3mf.pack_plates(objs, stl_dir, bed)` packs by real footprint and needs shapely.
- **Per-object keys:** any slicer setting, plus `'_filament': slot` and `'_blockers': [((x0,y0,z0), (x1,y1,z1)), …]`, in model coordinates.
- **Filaments:** `FILAMENT = 'PETG Basic'`, `COLOURS = ['#1A1A1A']` reduces the template to one preset, with one slot per colour. `bambu3mf.filament_names(ps)` lists what the template has. Bed size comes from the template.

The first run creates the project. Later runs use `sync3mf.sync`: new meshes go in place, and anything changed by hand in Bambu stays and is reported. If it refuses, read the conflicts; `accept_loss=True` only with the user's OK.

Never rebuild an existing project from the template, even from a script you wrote yourself: that wipes the filament, colours and settings the user chose in Bambu, and they cannot be recovered. Before an update, compare the saved file with the last build to see what the user changed, and commit their version first if the project is in git.

## Settings

- **Global first.** What is the same on every part goes in `PROJECT`. Per-object settings are for real exceptions only.
- **Test prints are cheap:** preset walls and about 10 % infill, keeping supports, brim and hole compensation, since those affect fit.
- **Supports:** by default `enable_support=1`, `support_type=tree(auto)`, `support_on_build_plate_only=0`, `support_remove_small_overhang=0`. An overhang on an exported part is a slicer problem first: turn on supports before proposing a redesign.
- **Holes lying flat** fill with tree supports and come out closed. Give each a `_blockers` box reaching at least 1.5 mm past the roof it protects, in the print-up direction. Blockers stop overhangs inside them only; branches from a nearby roof can still enter.

## Plates

- Name every plate. Never leave one empty.
- Start with a plain status: `[TODO]`, `[OK]`, `[FAIL]`, `[CHANGED]`. Keep names short, because Bambu cuts them off, and put a count near the front: `2x link`.
- A part printed N times is **one object**. The count goes in the plate name. When the design holds the N copies as identical bodies of one component, export one: `"one": true` in `parts.json`.
- Never drop a part from the project because it is printed. Mark the plate `[OK]`. The project is the parts list.
- One layer height per plate. Keep parts with different settings two brims apart, or Bambu reports G-code conflicts.
- Bambu Studio crashes at a 37th plate. Split by purpose into several projects: test, production.
- Some printers have an exclusion zone, such as the clumping-detection strip at the back of the P2S (X 153–216, Y 235–256). Keep it clear: a part over it fails the slice with "<object> is too close to clumping detection area". `add_plate.py` refuses a layout that leaves the bed, overlaps or covers a keep-out area; place objects with `--at "<object>=x,y"` or `--cols n`.
- Adding plates can move earlier ones: the grid has `ceil(sqrt(n))` columns, so plate 3 sits in another cell once there are 5. `add_plate.py` moves every plate's objects to its new cell.

## Print pauses

For an insert, bearing or nut that the print closes over:

- `add_plate.py … --pause <z1>,<z2>` adds pauses on the new plate. The height is `top_z`, a multiple of the layer height.
- Bambu pauses **before** printing the layer whose top is `top_z`: at the pause the part stands one layer lower. Use the top of the pocket for `top_z`; the insert must not stand above it.
- Put a part that needs pauses alone on its plate, since the pause stops every object on the plate.
- Check in the slice: `slice_check.py` prints each pause with the height printed at that moment.
- Thin loose pieces that go in at a pause (shims, holders) print first, as their own plate.

## Verify

```bash
python3 bambulab/tools/slice_check.py bambulab/Project.3mf
```

This is safe with the GUI open, and reports grams per plate. On PLA plates it also flags layers whose time is far from the typical layer (see below). **A passing CLI slice does not prove the GUI opens the file**: the GUI validates more strictly. After the first build of a new project, and after filament changes, have it opened in the GUI, and say plainly if that has not happened. Reopen Bambu only if it was already running.

## Even layer times on PLA

On PLA, a layer that takes much longer or much shorter than its neighbours cools differently, and the outer wall shows a band or gloss change at that height. Bambu's default minimum layer time for PLA is only 4 s, so small tops print in seconds while walls take half a minute. PETG, PETG-CF and PA6-CF don't show this; leave them alone.

Aim for wall layers within about 1.5x of each other. `slice_check.py` prints the layers outside that, with their Z, and Bambu shows the same in Preview > Color Scheme > Layer Time.

- **Too short** (small tops, thin towers): raise the filament's `slow_down_layer_time` to about the typical layer time from `slice_check`, and lower `slow_down_min_speed` to 10 so the slowdown can reach it: `bambu3mf.set_filament(ps, slot, {'slow_down_layer_time': '28', 'slow_down_min_speed': '10'})`. Put this in the project, not per object.
- **Too long** (an internal floor or a solid top part-way up): speed can't fix these. Tell the user where the band will be. A design fix is a floor that starts on the bed, so the long layers sit at the bottom edge; or make the floor solid all the way through so there is one long stretch instead of a sparse-to-solid jump.
- Report the extra print time from slowing down.

Re-run `slice_check.py` after the change.

## Other tools

| Tool | Use |
|---|---|
| `add_plate.py` | Add a plate, leaving the others untouched. |
| `update_3mf.py <proj> <stl-dir>` | Swap meshes in a project made by hand in Bambu Studio, keeping position and settings. Not for `build_3mf.py` projects: their meshes are stored already turned onto the bed, so a new mesh would stand in its Fusion orientation. Run `build_3mf.py` instead. |
| `set_3mf_setting.py` | Per-object settings from the command line. |
| `split_plates.py` | Move plates into a new project. |
| `wall_thickness.py` | Find thin walls and necks in STLs. numpy, scipy. |
| `bambu_template.py show` | What a template or project is set to. |

Library calls: `bambu3mf.set_filament(ps, slot, {...})` for filament overrides, `slow_cooldown(end_gcode)` for materials that warp when the bed goes cold, `purge_stl(path, tube_mm)` for a square that uses up wet filament in the feed tube.
