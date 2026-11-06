---
name: fusion-export
description: Export printed parts from Autodesk Fusion to STL with checks, and find what changed since the last export. Use when exporting STLs from Fusion, sending a Fusion design to Bambu Studio, checking whether STLs are stale, or checking parts for thin features and slivers.
---

# Fusion to STL

Everything here is `fusion/tools/fusion_parts.py`, run inside Fusion. If the project lacks it, run the `setup-project` skill.

## List the parts

`fusion/parts.json` maps each STL name to a body. Keep the names plain ASCII.

```json
{
  "bracket": {"path": ["Assembly:1", "Bracket:1"]},
  "cube":    {"body": "Cube"},
  "lid":     {"path": ["Box:1"], "body": "Lid"}
}
```

`path` is the occurrence path from the root, and `body` picks a named body. Without `body`, the occurrence must have exactly one visible body. A single-part document uses `body` alone. Hardware and reference components stay out.

## Run it

Pass this as the script of `fusion_mcp_execute` (`featureType: "script"`), with the project's absolute path. Use `readOnly: true`, since none of the three edits the design:

```python
P = r'<project>/fusion/tools/fusion_parts.py'
g = {'__file__': P}; exec(open(P, encoding='utf-8').read(), g); run = g['run']
```

| Entry | Does |
|---|---|
| `g['thin']` | Narrow planar faces under 1.2 mm, and parts that are not one clean solid. Must end `TOTAL 0`. |
| `g['stale']` | Parts whose B-rep changed since their export. |
| `g['run']` | Writes `stl/<name>.stl`. Call again until it prints `DONE`. |

To limit work to some parts, add `g['ONLY'] = ['bracket']` after the `exec`.

## Rules

- **Export refuses a document with unsaved changes.** The saved version number is the stamp in each STL header and in `bambulab/fusion_export.json`. Ask the user to save, or save only if they have told you to. Other sessions dirty the document within seconds, so save and export in one script.
- A part that fails its checks is **not written**. Read why: open edges and extra shells usually mean a real modelling defect, not a mesh problem.
- Move every pose joint to 0 first. Bed orientations assume the model pose.
- A `thin` hit can be intentional, such as a part designed with 0.5 mm walls. Say so to the user instead of forcing `TOTAL 0`.
- `thin` sees narrow faces, not thin material between wide faces. Follow it with the STL check, which needs numpy and scipy:

```bash
python bambulab/tools/wall_thickness.py --min 1.5 stl/*.stl
```

A neck it reports is always real. A sheet needs a look, because walls designed at the limit read slightly under it.

Next: the `bambu-project` skill.
