---
name: print-design
description: Design rules for parts that will be 3D printed on an FDM printer. Use when deciding a part's shape, print orientation, tolerances, press or sliding fits, heat-set inserts, screws, or strength, when planning test prints, and when a printed part comes out flawed.
---

# Designing for the print

Read the project's `DESIGN_RULES.md` first, and cite the rule behind each decision. It outranks this file: its numbers come from that printer.

## First: does it already exist?

For a common object, search Printables and MakerWorld before modelling, and report licences. Model it when the user wants their own or the fit is specific.

## Print-friendly by default

Every shape you design is meant to print well without supports, unless the user says otherwise:

- No downward face steeper than 45° from vertical, and no hanging parts. Slope or chamfer at 45° instead.
- If a side clearly has to go on the bed (a flat mounting face, a tile side, a flange), orient for that from the start. Do not model first and pick the orientation afterwards.
- Taking on a support, an overhang or a bridge to match a look or a reference is a trade-off. Ask the user first, saying what it buys and what it costs.

On custom parts a 45° slope almost never matters to the function, but it does improve the print. So 45° wins unless the user chooses otherwise.

## Order of decisions

1. **Print orientation first.** One flat bed face, nothing below it. Small features rise from the bed side, never mid-height on a wall.
2. **Load direction.** Lay layers along bending members. Never put tension across layers.
3. **Then the shape.** Use the free space for strength, in one solid piece. Count the screws: at least 4 on any joint that carries torque or tipping.

Surfaces that must be accurate go on the bed or face up.

## Overhangs

- Use **45°** chamfers on downward faces.
- Two 45° faces sloping different ways meet in a shallower edge, which the slicer supports. Check edges, not only faces. This is why a pyramid under a square corner looks faceted and prints worse at its ridge: a rounded outline with a taper or loft gives one smooth surface. A loft is fine when its steepest line, usually at a corner, is still 45°. Set its height from that line.
- Where a rounded part meets a sharp part above it, the sharp corner hangs out past the round one: a small overhang. Give both the same corner radius.
- Bridges up to about 6 mm.
- Do not change a part that is already exported or printing just to fix an overhang, unless asked.

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

## Copying an existing part

When the job is a replica of a real part:

1. **Measure before modelling.** Photos against a ruler are about 5 % off when the ruler and the part are at different heights; use them for shape, a caliper for numbers. Ask for one caliper value per part rather than guessing.
2. **Ask with pictures.** Draw numbered arrows and circles on the user's own photo of the part, with a line per number saying what to measure. Answers then come back as a numbered list, with no doubt about which edge was meant.
3. **Record each number with its source** (caliper, photo, owner) in the project, and keep a separate list of where the copy deliberately differs and why: a bed that is too small, a screw that can't be bought.
4. **Compare every part with photos of the original**, one part at a time, from renders that show the whole part. Check each claimed difference again before acting on it. Small things hide here: an opening filled in, a bowed edge modelled straight, a rib a third of its real height.

## Parts that mate

- Derive a pocket's depth from the thickness of the part that sits in it; never type the number twice. A pocket a few tenths too shallow leaves that part standing proud.
- Two parts that lie on each other take their outline from one function, so a change to one moves both.
- An insert longer than the material it sits in pokes through the other face. Check insert length against floor thickness, and thicken the floor (and what sits on it) if needed.
- **Choose each screw length, then check both ends.** Take a length that is sold (for M3: 6, 8, 10, 12, 14, 16, 20, 25 mm), then check that the tip ends inside the insert or nut and short of the pocket floor, and that the head sits 0.5–2 mm below the surface. If no stocked length fits, deepen the counterbore or fill under the head, rather than leaving the head proud or the tip on the floor. Count the thread engaged: a screw that grips only 2–3 mm of a 4 mm insert pulls out under load.
- Swapping a fastener for a shorter or longer one moves everything stacked on it by the difference: what sits on it rises or drops, and what sticks out changes length. Work it out and tell the user.

## Shafts, clamps and adjusters

- **A small shaft or stem under torque or bending snaps along a layer line if it was printed standing up.** Strong filaments do not save it: the layer bond is the weak plane. Lay it flat so the layers run along it, or carry the load on a metal pin or a bought shaft.
- **A friction clamp held by one screw on its own rotation axis works loose.** Every turn of the shaft works on that screw. Use at least two screws off the axis, or lock the angle with shape (flats, a key, teeth), not friction alone.
- **An adjuster without a stop gives no feedback.** If the user cannot tell when it is tight enough, it ends up too loose or broken. Give it a scale, a hard stop at the target, or a measured tightening torque.
- **Printed tools need real sections.** Thin tangs on a printed key or fork break on the first firm turn. Use a hex or flats of at least a few millimetres, or a bought hex key.
- **Check a spring's stroke, not only its force.** Small disc springs give high force over only a tenth of a millimetre or so, less than the tolerance of a printed stack, so they cannot set a preload there. Compare the stroke with the tolerance it has to take up.

## Fine relief on the first layer

Thin raised lines printed face down peel off the bed one by one: there is nothing between them. Join them into a net: union the relief with a copy of itself turned about the centre, so the lines cross. On a pattern with 4-fold symmetry turn by 45°; 90° lands the copy on itself.

## Test, then commit

1. Print in cheap material first (PLA, about 10 % infill), for fit only.
2. Measure: seats, insert pockets, gaps between parts fixed in two places, warp on large plates.
3. Write the results in `DESIGN_RULES.md` tagged **[M]**. Nothing is [M] until a real print says so.
4. If a fit is uncertain, print a small coupon with three clearance steps, marked 1, 2, 3, before the whole part.
5. Correct in the slicer, or on the cheapest part, then print the final material. Materials shrink differently, so a PLA sliding fit can bind in a filled nylon.

## Warping

Large parts in nylon blends lift at the corners while the brim stays down. A wider brim does nothing, because shrink in the layers above does the pulling. Cut the part fan to 0–10 %, use fewer walls and lower infill where strength allows, round sharp corners on the bed outline, and cool the bed slowly (`bambu3mf.slow_cooldown`). Dry the filament.

## Visible PLA parts

Layers that take very different times show as bands on PLA walls (see "Even layer times on PLA" in the bambu-project skill). For parts where looks matter, avoid an internal floor or shelf part-way up a visible wall: start floors on the bed, or accept a band at that height and say so.

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
- [ ] Shafts and stems lie along their layers; clamps on a rotating axis have more than one screw or a shape lock
- [ ] `thin` ends `TOTAL 0` and `wall_thickness.py` shows no neck
- [ ] Mating parts checked against each other, stationary ones too, through the full travel of anything that moves
- [ ] Close-up screenshots looked at
- [ ] Every asymmetry and every "had to" change named to the user
