---
name: gate
description: The print gate - deterministic checks of Fusion parts before they are printed. Heat-set insert pockets, and overhangs / supports (with a Bambu Studio slicer cross-check). Use before and after changing a printed part, when placing or fixing heat-set inserts or their pockets, when a part needs supports or its print side is in question, when gate.py reports findings, and when adding a new gate to the plugin.
---

# The print gate

The gate reads Fusion and judges in Python. It finds the problem; you only decide how to fix it. Never measure by eye
or by your own script what the gate measures: run it.

- **Rules live in the plugin only:** `rules/<gate>.json` (`rules/heatset.json`, `rules/overhang.json`), with the incident or the print
  behind each number. A project never copies them.
- **A project keeps only its own:** `gate/settings.json` (optional overrides, merged key by key over the plugin's rules)
  and `gate/results/<gate>/<part>.json` (one file per part per run: project, Fusion document and version, the body's
  faces, volume and area, the plugin version, the hashes of the rules and of the settings, the verdict and every
  finding). A part counts as checked only while its file says `pass` and its body is still the one measured.
- **The parts** come from the project's `fusion/parts.json` (the same file `tools/export.py` uses).
- **Export does not wait for the gate:** `tools/export.py` sends what Fusion has to Bambu as it is. Run the gate before
  you call a part ready to print, and tell the owner every open finding of a part you changed.

## Commands

The plugin's own copy is the one to run; nothing is vendored into the project.

```bash
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py check [PART ...]
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py fix-inserts PART ... [--component NAME] [--dry-run]
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py rules
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py overhang [PART ...]
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py overhang-slice [PART ...]   # minutes: run in the background
```

Run them from the project folder (or pass `--project DIR`). Exit 0: every checked part passes; 1: findings; 4: the gate
could not run (Fusion does not answer, a part was not found). On Robot Arm V2 (39 parts, 200 inserts) `check` takes
about 3 s.

## The heat-set gate

What it checks, from the B-rep of each printed body and every insert body in the document:

1. **Every pocket has the profile of the table**, read from its mouth inwards: melt relief D7.5 (or D6.5, D5.9) x 0.54,
   lead-in D4.4 x 1.5, hole D3.6 to 6.5 below the relief floor (or through). A wider recess at the mouth is skipped:
   the relief belongs in its floor. A counterbore for a screw (D6.5 then D3.4) is not a pocket.
2. **Every insert is the golden model:** `Heat-set insert M3x4x5 (golden)`, drawn from the table: pilot band D4.2 x 1.0
   at the end that goes in first, knurl D5.0 up to 4.0, bore D3.0. Any other shape is `not_golden`.
3. **Every insert sits in its pocket:** on the pocket's axis, its top flush with the relief floor, the pilot band down.
4. **Every pocket has an insert, every insert a pocket.** An insert in a body that is not in `fusion/parts.json` is
   counted as outside the printed parts (its host is not checked); the summary names them.

How it finds them: every concave cylinder of a printed body, grouped by axis line; a group is cut where the axis runs
through plastic or across a cavity; the ends of each hole are tested open or closed with `pointContainment`. An insert
belongs to the printed body that has plastic round it.

## Findings and their fixes (heat-set)

Each finding has `measured`, `expected`, `fix`, the mouth `at` (world mm) and `out` (the axis out of the material) — the
point and axis `tools/fusion/insert_pockets.py` `cut()` and `relief()` take.

| Code | Means | Fix |
|---|---|---|
| `relief_missing` | the pocket opens straight onto the face | `insert_pockets.relief()`: D7.5 x 0.54, D6.5 / D5.9 where D7.5 does not fit |
| `relief_d`, `relief_depth` | a relief of another size | recut the relief to the table |
| `lead_in_missing`, `lead_in_d`, `lead_in_depth` | no D4.4 x 1.5 lead-in, or another one | `insert_pockets.convert()` (old straight pocket) or `cut()` |
| `hole_d`, `hole_missing` | the hole under the lead-in is not D3.6 | recut the pocket from the table |
| `hole_short` | the D3.6 hole ends less than 6.5 below the relief floor | deepen it (room for the displaced plastic and the screw tip) |
| `open_wall` | the pocket wall is not a full circle | close the wall |
| `not_a_pocket` | an insert sits in a hole that is not a pocket at all | cut a pocket of the table there |
| `no_insert`, `not_golden`, `not_flush`, `upside_down`, `off_axis`, `two_inserts` | the insert model | `gate.py fix-inserts PART` (places or replaces golden inserts on the relief floor; the extra of two is deleted by hand) |
| `insert_without_pocket` | an insert in plastic with no hole on its axis | cut the pocket, or move the insert |

`fix-inserts` changes only insert bodies (one base feature per call, in the component of the replaced insert, or in
`Heat-set inserts (golden)` for new ones). Pockets are cut by `insert_pockets.py`; after any fix run `check` again.

## The overhang gate

**One print side per part.** The bed face of a part is the `face` of its `3mf` entries in `fusion/parts.json` (`-z`,
`+y`, ...: the model face that lies on the bed, as `bambu3mf.ORIENT` turns it). It is the only source of which side
prints down; the part's design depends on it. No face, or two different faces, is a finding.

`gate.py overhang` (one read-only Fusion call, about a minute for 39 parts; it reads the heat-set facts in the same
call) turns each body with its bed face down and judges in `overhang_core.py`:

| Code | Means | Fix |
|---|---|---|
| `overhang` | a downward face steeper than `limit_deg` (45 from the vertical), not a bridge, above the first layer | a 45 degree rise from below, or lay the part on another side |
| `round_hole` | a round hole with a horizontal axis: its top needs a support | a teardrop top (45 degree roof); only heat-set pockets stay round |
| `long_bridge` | a flat ceiling held on both sides by a span longer than `bridge_max_mm` (6) | narrow it, or a 45 degree roof |
| `shallow_ridge` | two downward faces that pass meet in a convex edge rising less than the limit (two 45 degree chamfers round a corner: 35 deg) | the same tilt on both faces so the ridge runs level, or 45 degrees along it |
| `no_bed_face`, `bed_face_conflict` | `fusion/parts.json` does not name one print side | name it |
| `heatset_ceiling` | **allowed**: the ceiling of a heat-set pocket that opens sideways or down (the insert needs the round pocket) | listed, not failed |

A flat ceiling is a bridge where a straight chord through it, ending on both sides at an edge where the material goes on
downward, is at most `bridge_max_mm`; a cantilever edge holds nothing, except within `ledge_mm` (0.4) of a held edge,
where the wall line still sits on the layer below. A flat face thinner than `sliver_mm` on average is a modelling
sliver (info, not an overhang). Normals are the surface's own, not the mesh facets' (a 45 deg cone is 45). A pocket
is the heat-set gate's pocket (mouth to the end of its hole). Patches under `min_area_mm2` (1) are counted, not listed. Each region in
`gate/results/overhang/<part>.json`: `at` (world mm), `box`, `area_mm2`, `slope_deg` (0 = flat ceiling, 90 = wall),
`h` above the bed; bridges and small patches go to `info`.

**The slicer's opinion** (`overhang-slice`): copies of the parts' `.3mf` projects are sliced with the Bambu Studio CLI in
a temp folder (the originals are only read: safe while Bambu Studio is open), the tips of the support columns are read
from the G-code per object and carried back into the model frame, and compared with the regions of the last
`overhang` run. Per part (`"slicer"` in its result, `_slicer.json`): support line, contact area vs the area the gate
expects, each region `supports` / `no support` (with `why` when the project's `support_threshold_angle` or a support
switched off explains it), and `slicer_only` clusters (support where the gate sees nothing: a missed shape, or a bridge
the slicer supports anyway). Disagreement both ways is reported; neither side alone is the truth. A part whose sliced
outline differs from the body (a stale mesh) is an error, not a comparison: export first. In the copy every object is
printable and the prime tower is off; a support blocker of the project, the bed gap and the threshold angle are named
as reasons. On Robot Arm V2 (2026-11-24) 38 of 39 parts agreed; the slicer also put support under hundreds of bridges
the gate allows (the projects have `bridge_no_support` = 0).

## Adding a gate

One gate is one question with a deterministic answer. Before it goes into the plugin:

1. Its numbers go into `rules/<gate>.json` with where they come from (a print, a datasheet, a decision).
2. A pure-Python judge with unit tests on hand-made facts (`tests/test_<gate>_core.py`): one test per way it can fail.
3. A Fusion fixture document built by a script with every case, and an end-to-end test against it
   (`tests/test_<gate>_fusion.py`, skipped without Fusion): each case gives exactly its findings, look-alikes give none.
4. A run on a real project: every finding checked by hand, no false positive left, and a coverage count in both
   directions (every feature found, every finding real). Fast: a few seconds for a whole project.
5. Every finding says what was measured, what is expected and how to fix it.
