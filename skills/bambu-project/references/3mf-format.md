# Bambu .3mf internals

A project is a zip. These facts were found by breaking it.

## Files

| File | Holds |
|---|---|
| `3D/3dmodel.model` | build items and their transforms |
| `3D/Objects/object_N.model` | meshes |
| `Metadata/model_settings.config` | objects, per-object settings, plates |
| `Metadata/project_settings.config` | JSON: printer, process and per-filament settings |
| `Metadata/filament_sequence.json` | one entry per plate |
| `Metadata/custom_gcode_per_layer.xml` | pauses |

## Rules

- **`different_settings_to_system`:** Bambu resets a global override on load unless the key is listed here. Slot `[0]` is process, the middle slots are one per filament, `[-1]` is printer. `bambu3mf.set_project` and `set_filament` handle this.
- **Per-filament arrays have a stride** of 1 to 4 values per filament (`filament_keys.json`), and `flush_volumes_matrix` is n×n. To add or drop a filament, change all of them: `only_filament` and `add_filament`.
- **`filament_self_index`** holds one entry per extruder variant. A short list loads in the CLI, but the GUI says "Invalid configuration file", then "no geometry".
- **`filament_sequence.json`** needs one entry per plate. Bambu otherwise keeps the template's plates.
- **Part ids** in `model_settings.config` must be the component `objectid`s from the model. With ids 1, 2, 3, Bambu ignored `subtype="support_blocker"` and printed the blocker boxes as solid parts.
- **Mesh precision:** weld vertices on exact float32 values and write `%.9g`. Rounding to 6 digits made non-manifold edges.
- **"Unchanged" mesh:** same triangle count, bbox size and volume within 0.001 mm³ absolute. A relative check missed a 10 mm³ change.
- **Plate grid:** plates sit on a grid with a 20 % gap, `ceil(sqrt(n))` columns, rows going −Y. Object transforms are world coordinates on that grid.
- **Names** must not contain `< > : / \ | ? * "`, or Bambu shows an error dialog on open. `bambu_name` cleans them.
- **Quitting:** quitting Bambu during startup crashed it. Let it run 30 s first.
- **Reloading:** Bambu does not watch files, and opening the same path again does not refresh an open project. It must be closed and reopened. A `* ` at the start of the window title means unsaved changes.
- **No config:** a plain `.3mf` with no `project_settings.config` opens with "invalid config, load geometry data only". Always start from a saved project.
- **Prime tower on a two-filament plate:** "gcode unprintable error_code 4" with fixed placement means the tower is off the bed. Set `wipe_tower_x` and `wipe_tower_y`.
- **Height:** objects slice at the height they sit, so a mesh must start at z = 0.
- **Global-only keys** set on an object are ignored without a warning.
- **CLI-written projects:** a project exported by the CLI from unflattened profiles falls back to defaults (500 mm/s² acceleration, filament density 0, Cool Plate, a 200 mm bed). Starting from a GUI-saved template avoids this.
- **`T65535` in a P2S log** is normal. Look for the real failure elsewhere.

## Inspecting without the GUI

```bash
BambuStudio --slice 0 --outputdir <dir> <project.3mf>
BambuStudio --outputdir <dir> --export-3mf re.3mf <project.3mf>
```

The first writes G-code per plate and `result.json`. The G-code header shows only global settings, though per-object ones still apply. The second writes the project back as Bambu reads it. Use it when Bambu shows solid material Fusion does not have: every blocker must come back as `subtype="support_blocker"`.

To check a support blocker, slice with `--slice <plate> --export-3mf`, then sum `; FEATURE: Support` extrusion inside the hole's box in `plate_<n>.gcode`.
