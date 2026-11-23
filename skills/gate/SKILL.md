---
name: gate
description: The print gate - deterministic checks of Fusion parts before they are printed. Today one gate, heat-set insert pockets. Use before and after changing a printed part, when placing or fixing heat-set inserts or their pockets, when gate.py reports findings, and when adding a new gate to the plugin.
---

# The print gate

The gate reads Fusion and judges in Python. It finds the problem; you only decide how to fix it. Never measure by eye
or by your own script what the gate measures: run it.

- **Rules live in the plugin only:** `rules/<gate>.json` (today `rules/heatset.json`), with the incident or the print
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
python3 ~/.claude/plugins/marketplaces/fusion-bambulab/tools/gate/gate.py check [PART ...]
python3 ~/.claude/plugins/marketplaces/fusion-bambulab/tools/gate/gate.py fix-inserts PART ... [--component NAME] [--dry-run]
python3 ~/.claude/plugins/marketplaces/fusion-bambulab/tools/gate/gate.py rules
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

## Findings and their fixes

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

## Adding a gate

One gate is one question with a deterministic answer. Before it goes into the plugin:

1. Its numbers go into `rules/<gate>.json` with where they come from (a print, a datasheet, a decision).
2. A pure-Python judge with unit tests on hand-made facts (`tests/test_<gate>_core.py`): one test per way it can fail.
3. A Fusion fixture document built by a script with every case, and an end-to-end test against it
   (`tests/test_<gate>_fusion.py`, skipped without Fusion): each case gives exactly its findings, look-alikes give none.
4. A run on a real project: every finding checked by hand, no false positive left, and a coverage count in both
   directions (every feature found, every finding real). Fast: a few seconds for a whole project.
5. Every finding says what was measured, what is expected and how to fix it.
