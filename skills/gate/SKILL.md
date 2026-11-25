---
name: gate
description: The print gate - deterministic checks of Fusion parts before they are printed. Heat-set insert pockets, overhangs / supports (with a Bambu Studio slicer cross-check) and thin walls. Use before and after changing a printed part, when placing or fixing heat-set inserts or their pockets, when a part needs supports or its print side is in question, when a wall, a rib, a hole near an edge or a sliver may be too thin, when gate.py reports findings, and when adding a new gate to the plugin.
---

# The print gate

The gate reads Fusion and judges in Python. It finds the problem; you only decide how to fix it. Never measure by eye
or by your own script what the gate measures: run it.

- **Rules live in the plugin only:** `rules/<gate>.json` (`rules/heatset.json`, `rules/overhang.json`,
  `rules/thinwall.json`), with the incident, the print or the decision behind each number (`source`). A project never
  copies them.
- **A project keeps only its own:** `gate/settings.json` (optional overrides, merged key by key over the plugin's rules)
  and `gate/results/<gate>/<part>.json` (one file per part per run: project, Fusion document and version, the body's
  faces, volume and area, the plugin version, the hashes of the rules and of the settings, the verdict and every
  finding). A part counts as checked only while its file says `pass` and its body is still the one measured.
- **The parts** come from the project's `fusion/parts.json` (the same file `tools/export.py` uses).
- **Export goes through the gates:** `tools/export.py` runs the gates the project requires (`gate/settings.json`
  `"export": {"require": [...]}`, default every gate) on the parts it exports and holds back each part that fails,
  with exit 1. No switch skips a gate. To make a gate required in a project, first bring every part to 0 findings of
  it (fix the model; a named exception with its reason where the gate takes them), then add it to `require` and run
  the export of all parts once. Tell the owner every open finding of a part you changed.
- **"slicer" in `require`** (needs "overhang"): after the STLs are written, Bambu Studio slices only the plates of the
  exported parts with the new meshes (`overhang-slice`, about 15-20 s a plate; every part takes minutes) and a part the
  slicer still supports (`slicer_support` / `slicer_bridge`) is held back, its old STL put back.
- **A run on some parts** writes `_last_run.json`; only a run on all parts writes `_summary.json`.

## Commands

The plugin's own copy is the one to run; nothing is vendored into the project.

```bash
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py check [PART ...]
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py fix-inserts PART ... [--component NAME] [--dry-run]
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py rules
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py overhang [PART ...]
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py overhang-slice [PART ...]   # named parts: only their plates, seconds; no part: every plate, minutes
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py overhang-faces [PART ...]   # every bed face of the failing parts
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py check --gate thinwall [PART ...]
python3 ~/.claude/plugins/marketplaces/print-engineering/tools/gate/gate.py rules --gate thinwall
```

Run them from the project folder (or pass `--project DIR`). Exit 0: every checked part passes; 1: findings; 4: the gate
could not run (Fusion does not answer, a part was not found). On Robot Arm V2 (39 parts, 200 inserts) `check` takes
about 3 s; `check --gate thinwall` about 15 s (6 s in Fusion, the rest judging on all cores).

## The heat-set gate

What it checks, from the B-rep of each printed body and every insert body in the document:

1. **Every pocket has the profile of the table**, read from its mouth inwards: melt relief D7.5 (or D6.5, D5.9) x 0.54,
   lead-in D4.4 x 1.5, hole D3.6 to 6.5 below the relief floor (or through). A wider recess at the mouth is skipped:
   the relief belongs in its floor. A counterbore for a screw (D6.5 then D3.4) is not a pocket.
   **A mouth on a face that is down in print** (the bed face) takes the relief as a 45 deg countersink from the relief
   diameter at the face to D4.4: the flat ring ceiling of the cylinder hangs 0.54 over the bed where no support reaches
   (Bambu: "overhang regions that could not get support"). The insert top stays 0.54 below the face, inside the cone;
   the lead-in counts from there (`rules/heatset.json` relief.cone).
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
| `exception_without_reason`, `exception_unused` | an exception in `gate/settings.json` with no reason, or at a place with no finding | write the reason, or remove it |
| `heatset_ceiling` | **allowed**: the ceiling of a heat-set pocket that opens sideways or down (the insert needs the round pocket) | listed, not failed |

A flat ceiling is a bridge where a straight chord through it, ending on both sides at an edge where the material goes on
downward, is at most `bridge_max_mm`; a cantilever edge holds nothing, except within `ledge_mm` (0.4) of a held edge,
where the wall line still sits on the layer below. A flat face thinner than `sliver_mm` on average is a modelling
sliver (info, not an overhang). Normals are the surface's own, not the mesh facets' (a 45 deg cone is 45). A pocket
is the heat-set gate's pocket (mouth to the end of its hole). Patches under `min_area_mm2` (1) are counted, not listed. Each region in
`gate/results/overhang/<part>.json`: `at` (world mm), `box`, `area_mm2`, `slope_deg` (0 = flat ceiling, 90 = wall),
`h` above the bed; bridges and small patches go to `info`.

**Supports are a design failure by default.** Every finding carries a `redesign` (teardrop, 45 deg chamfer or rib from
below, steepen, chamfer instead of a rounded bottom edge, take a low ceiling to the bed, split or roof a long bridge,
level a ridge) with its place; report it with the finding. `overhang-faces` judges all six bed faces of the failing parts
(findings and flat bed contact per face): say when another face clears findings. An overhang passes only as a named
exception the owner decides, in the project's `gate/settings.json`:

```json
{"overhang": {"exceptions": [{"part": "j1_magnet_carrier_test_ring", "at": [12.0, 0.0, 4.8], "reason": "standard 60 deg thread flank"}]}}
```

(`face` = the face index instead of `at`; `"whole_part": true` instead of both when the owner calls the part itself an exception; `code` narrows it.) It shows under `excepted` with its reason; no reason, or
no finding left at the place, is a finding. Never add an exception yourself: list the finding, its redesign, and why a
redesign is impossible if it is. Every finding you put before the owner goes with a picture of its place:
`tools/fusion/show_part.py PART --gate overhang` (see "Asking the engineer about a part" in the `fusion-modeling` skill).

**The slicer's opinion** (`overhang-slice`): copies of the parts' `.3mf` projects are sliced with the Bambu Studio CLI in
a temp folder (the originals are only read: safe while Bambu Studio is open), the tips of the support columns are read
from the G-code per object and carried back into the model frame, and compared with the regions of the last
`overhang` run. Per part (`"slicer"` in its result, `_slicer.json`): support line, contact area vs the area the gate
expects, each region `supports` / `no support` (with `why` when the project's `support_threshold_angle` or a support
switched off explains it), and `slicer_only` clusters (support where the gate sees nothing: a missed shape, or a bridge
the slicer supports anyway). Disagreement both ways is reported; neither side alone is the truth. A part whose sliced
outline differs from the body (a stale mesh) is an error, not a comparison: export first. In the copy every object is
printable and the prime tower is off; a support blocker of the project, the bed gap and the threshold angle are named
as reasons. The slicer leaves a bridge without support when `bridge_no_support` = 1 (every bridge) or when it is no longer than
`max_bridge_length`; set `max_bridge_length` to the gate's `bridge_max_mm` so the two judge bridges alike
(`_slicer.json` names a project where they differ, `bridge_note`). Robot Arm V2: both projects `max_bridge_length` = 6,
`bridge_no_support` = 0 since 2026-11-24; before that the slicer put support under hundreds of bridges the gate allows.

Some ceilings the geometry passes as bridges Bambu supports **whatever the bridge settings** (a copy sliced with
`bridge_no_support` = 1 kept the same support): flat ceilings over tooth gaps under a pulley flange, the sacrificial
bridges of some screw counterbores. So a bridge with at least `slicer.bridge_contact_mm2` of support contact on it
becomes the finding **`slicer_bridge`** (fails the part, with a redesign: a 45 deg roof up into it, a wall under its
open side, or another bed face). Each contact cell counts once, for the nearest allowed region within one cell of it.
These findings come from `overhang-slice` and stay in the part's result while the body is unchanged (`overhang` keeps
them); a changed body drops them until the next slice.

## The thin-wall gate

One Fusion call reads the display mesh of every face of each printed body (with the face's exact plane, cylinder, cone
or sphere); `thinwall_core.py` judges. From sample points every 0.5 mm of surface a ray runs into the material along
the inward normal; the face it leaves through, when it looks back at the start, ends the wall. Ends on analytic faces
are moved onto the exact surface, so a hole near a face reads its true wall, not the mesh's chord. Thin samples close
together (or on the same two faces) are one finding; each thin spot is sampled again 8 times finer round its thinnest
samples. Necks between two corners (a hole near the edge of a pocket) are measured by fans of rays from every
re-entrant edge; faces under 1 mm2 get samples of their own.

Limits, per part, from the projects `fusion/parts.json` puts it in (`"3mf": [{"file", "object", "face"}]`), the widest
line counting: the rulebook wall (`min_wall_mm`), two lines of the project's `outer_wall_line_width` unless the object
is set to `wall_generator = arachne`, one line in any case. Not reported, by named rule (counted in `counts`):

| Rule | What it is |
|---|---|
| `corner` | the ray left through a side face (more than 55 degrees off): it ran along a corner, not across a wall |
| `wedge` | the two faces taper by 20 degrees or more: the tip of a chamfer, a 45 degree flank, a bed knife edge of an overhang |
| `teeth` | both faces in rings of at least 20 equal faces round one axis of the part (not two holes): a gear or pulley profile |
| `coaxial` | (a rule that finds, not exempts) a boss and a hole on one axis whose radii differ by less than one line, side by side along the axis: a tube the mesh cannot see |
| `sacrificial_layer` | flat in the layer plane (bed face from parts.json), 1 or 2 layer heights thick: a bridge layer over a hole |

| Code | Means | Fix |
|---|---|---|
| `faces_cross` | two faces of the body cross: plastic on both sides of each (a boss 0.02 over a hole on one axis) | a modelling error: make the two diameters one |
| `under_one_line` | thinner than one line of the project: a sliver, a film between two holes, a knife edge under 20 degrees | thicken or remove; it cannot print as modelled |
| `under_two_lines` | thinner than two lines, classic walls | thicken; or arachne on that object in the .3mf + a fit test |
| `thin_wall` | thinner than the rulebook wall | thicken, move the hole, grow into free space; or the owner accepts it |

Each finding has `at` (the middle of the thinnest measurement, world mm), `across` (its direction), `thickness_mm`,
`limit_mm`, every limit, the project, `faces` (index, type, area, D for cylinders) and `exact` (both ends on analytic
surfaces; a torus or free-form face is measured on its mesh). A thin spot the owner keeps goes into the project's
`gate/settings.json`: `{"thinwall": {"accept": [{"part", "near": [x, y, z], "within_mm", "code", "why"}]}}`; it is then
listed as accepted, not as a finding. Numbers can be overridden there too (`"thinwall": {"limits": ...}`).

Not covered: gaps that are too narrow (slits), walls between two parts, anything in a body outside `fusion/parts.json`.
The independent audit (`tests/audit_thinwall/`: B-rep minimum distances between faces, no code of the gate) is run after
any change of the gate.

## Adding a gate

One gate is one question with a deterministic answer. Before it goes into the plugin:

1. Its numbers go into `rules/<gate>.json` with where they come from (a print, a datasheet, a decision).
2. A pure-Python judge with unit tests on hand-made facts (`tests/test_<gate>_core.py`): one test per way it can fail.
3. A Fusion fixture document built by a script with every case, and an end-to-end test against it
   (`tests/test_<gate>_fusion.py`, skipped without Fusion): each case gives exactly its findings, look-alikes give none.
4. A run on a real project: every finding checked by hand, no false positive left, and a coverage count in both
   directions (every feature found, every finding real). Fast: a few seconds for a whole project.
5. Every finding says what was measured, what is expected and how to fix it.
