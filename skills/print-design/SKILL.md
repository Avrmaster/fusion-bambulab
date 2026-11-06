---
name: print-design
description: Design rules for parts that will be 3D printed on an FDM printer. Use when deciding a part's shape, print orientation, tolerances, press or sliding fits, heat-set inserts, screws, or strength, when planning test prints, and when a printed part comes out flawed.
---

# Designing for the print

Read the project's `DESIGN_RULES.md` first, and cite the rule behind each decision. It outranks this file: its numbers come from that printer.

## First: does it already exist?

For a common object, search Printables and MakerWorld before modelling, and report licences. Model it when the user wants their own or the fit is specific.

## Order of decisions

1. **Print orientation first.** One flat bed face, nothing below it. Small features rise from the bed side, never mid-height on a wall.
2. **Load direction.** Lay layers along bending members. Never put tension across layers.
3. **Then the shape.** Use the free space for strength, in one solid piece. Count the screws: at least 4 on any joint that carries torque or tipping.

Surfaces that must be accurate go on the bed or face up.

## Overhangs

- Use planar **45°** chamfers on downward faces. Avoid lofts.
- Two 45° faces sloping different ways meet in a shallower edge, which the slicer supports. Check edges, not only faces.
- Bridges up to about 6 mm.
- Tiny leftover overhangs get slicer supports, not a redesign. Do not change an exported or printing part for an overhang unless asked.

## Starting values

Measured on one Bambu P2S with a 0.6 mm nozzle. Treat them as a first guess, and replace them in `DESIGN_RULES.md` once measured.

| Feature | Start with |
|---|---|
| Hole sizes | model nominal; slicer X-Y hole compensation 0.125 |
| Sliding fit, printed on printed | 0.25 per side PLA, at least 0.4 per side PA6-CF, in the print plane only |
| Minimum wall | 1.5 mm; hole to edge 1.5 mm |
| Narrowest face | 1.2 mm |
| Plastic under a screw head | 5 mm |
| Counterbore | head + 0.5 to 2 mm: pick a longer screw, do not sink the head |
| M3×4×5 heat-set insert | straight Ø4.4 × 6.5 pocket under a Ø7.5 × 0.54 melt relief; open on a top face |
| Pegs into Ø2.2 holes | Ø2.0 |

Prefer hex-socket screws in standard lengths, and few of them.

Read bought-part sizes off their models, then **measure the real part**: vendor CAD can be off by a millimetre, and that one number decides a fit. A hole of Ø4.0 in a model means M4.

Scaling a model must hold its interfaces: scale the body, and keep screw bosses, bearing seats and magnet pockets at their real size.

## Test, then commit

1. Print in cheap material first (PLA, about 10 % infill), for fit only.
2. Measure: seats, insert pockets, gaps between parts fixed in two places, warp on large plates.
3. Write the results in `DESIGN_RULES.md` tagged **[M]**. Nothing is [M] until a real print says so.
4. If a fit is uncertain, print a small coupon with three clearance steps, marked 1, 2, 3, before the whole part.
5. Correct in the slicer, or on the cheapest part, then print the final material. Materials shrink differently, so a PLA sliding fit can bind in a filled nylon.

## Warping

Large parts in nylon blends lift at the corners while the brim stays down. A wider brim does nothing, because shrink in the layers above does the pulling. Cut the part fan to 0–10 %, use fewer walls and lower infill where strength allows, round sharp corners on the bed outline, and cool the bed slowly (`bambu3mf.slow_cooldown`). Dry the filament.

## A flawed print

Propose one of two classes, and let the user decide:

- **Critical:** the model changes and the part is reprinted before assembly.
- **Tolerable:** patch it by hand now, and put the model fix in for the next reprint.

Record both "now" and "next print" for each flaw, so the model fix is not forgotten. Never reprint a tolerable part unasked.

## Report what was actually checked

A result counts only if it comes from the current model. After any change, earlier checks prove nothing until they are run again. Report each check as passed, failed, or **not verified**, and never fold "not verified" into "passed".

## Check before calling a part done

- [ ] One flat bed face, and orientation noted
- [ ] Every hole and pocket clear after the last join
- [ ] `thin` ends `TOTAL 0` and `wall_thickness.py` shows no neck
- [ ] Mating parts checked against each other, stationary ones too, through the full travel of anything that moves
- [ ] Close-up screenshots looked at
- [ ] Every asymmetry and every "had to" change named to the user
