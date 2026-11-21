---
name: print-design
description: Design rules for parts that will be 3D printed on an FDM printer. Use when deciding a part's shape, print orientation, tolerances, press or sliding fits, heat-set inserts, screws, or strength, when planning test prints, and when a printed part comes out flawed.
---

# Designing for the print

Read the project's `DESIGN_RULES.md` first, and cite the rule behind each decision. It outranks this file: its numbers come from that printer.

In a project with a print gate (`gate/project.json`), use the `gate` skill as well. There `gate/RULES.md` holds the registered rules with that project's numbers, and `gate.py` checks them: a part is checked only when its stamp exists. In such a project a number has ONE home: a limit the gate checks lives in `gate/owner/params.json`, and `DESIGN_RULES.md` only points to it. A number written in `DESIGN_RULES.md` alone is checked by nobody.

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
| Wall between one and two line widths (a thin sleeve round a bore) | per-object `wall_generator = arachne`; classic walls lay two full lines into it and the bore closes (a 3.9 bore in a 0.875 wall measured 3.6) |
| Sliding fit, printed on printed | 0.25 per side PLA, at least 0.4 per side PA6-CF, in the print plane only |
| Minimum wall | 1.5 mm; hole to edge 1.5 mm |
| Narrowest face | 1.2 mm |
| Plastic under a screw head | 5 mm |
| Counterbore | head + 0.5 to 2 mm: pick a longer screw, do not sink the head |
| M3×4×5 heat-set insert | Ø4.4 × 1.5 lead-in, then Ø3.6 to 6.5 deep, under a Ø7.5 × 0.54 melt relief; open on a top face (see Heat-set inserts) |
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

## Heat-set inserts

Measured on M3×4×5 inserts (knurl Ø5.0, plain pilot band Ø4.2, length 4.0) in PETG-CF on a Bambu P2S, slicer X-Y hole compensation 0.125. The datasheet hole (Ø4.4) is too big once the slicer compensation is on.

| Pocket, as modelled | Printed (≈) | Result |
|---|---|---|
| straight Ø4.4 | Ø4.65 | holds almost nothing: pulled out by hand with a screw |
| straight Ø3.7 | Ø3.95 | grips well, but the insert goes in tilted |
| Ø4.4 × 1.5 lead-in + Ø3.6 | Ø4.65 / Ø3.85 | grips and goes in straight: **use this** |
| Ø4.4 × 1.5 lead-in + Ø3.4 | Ø4.65 / Ø3.65 | too tight: displaced plastic bulges into the insert's thread |

- **The sizes live in one place:** `POCKETS` in `${CLAUDE_PLUGIN_ROOT}/tools/fusion/insert_pockets.py` (vendored to `fusion/tools/` in a project). Projects use them and do not copy the numbers; a project writes its own value in `DESIGN_RULES.md` only when a print on its printer measured otherwise. The same file cuts new pockets (`cut`), converts old straight ones (`plan`, `convert`) and checks them (`audit`).
- **Pocket:** a lead-in a little over the pilot band (Ø4.4 for a Ø4.2 band), about as deep as that band, so the cold insert stands straight before it melts in; then a hole well under the knurl for the rest of the depth. Never a lead-in as wide as the knurl: the knurl grips nothing there.
- **Depth:** insert length + about 2.5 below the relief floor, so the displaced plastic and the screw tip have room.
- **Melt relief:** a shallow counterbore (Ø7.5 × 0.54 for M3, three layers) on the face the mating part lands on. The ring of plastic the insert pushes up stays in it instead of holding the mating part off. The insert ends flush with the relief floor. **Every pocket gets one**, also pockets converted from an older size (`convert()` changes only the hole, so check with `audit()`'s `no_relief` and add them with `relief()`). Where Ø7.5 leaves less wall beside it than the rule `inserts.wall_at_relief` asks, use Ø6.5, then Ø5.9; where none fits, no relief and shave the ring flush by hand after installing.
- **Wall:** the plastic round a pocket meets the wall target of the rule `inserts.wall` wherever the part has room. The floor of that rule is for a pocket squeezed between two features; it is not a target. Both numbers are in the registered rules below, and a project's own are in its `gate/RULES.md`. There is no pass value under the target: every pocket under it is a row. FAIL under the floor, GROW where nothing is in the way, BLOCKED by a named neighbour. A 2.5 wall with free space all round it bulged when the insert went in (PETG, 0.6 nozzle). **Fix a thin wall in this order:**
  1. Where the outline of the part is free on that side, make the whole wall or shoulder wider.
  2. Move the pocket together with its screw.
  3. A local boss only where the outline cannot move.
  4. Blocked on every side: report the row with what stops it. The owner decides, nobody else.

  Do not start at the boss: a boss on a wall that could simply be wider is the wrong fix. The gate measures the wall (`gate.py run`); `wall('<part>')` in `insert_pockets.py` is a quick look by hand, against solid bodies and mesh refs at three depths; it uses the plugin's default numbers unless you set `PARAMS` from `gate/owner/params.json`, and in a project with a gate the gate's row is the verdict. It sees bodies, not paths: where a part slides in or sweeps past, prove the free space with a sweep before growing into it. After the fix slice the plate, because added material must follow the print orientation. A tighter hole pushes harder on thin walls.
- **A missing relief blocks the export, like a thin wall.** `relief('<part>', dry=True)` lists every pocket that opens straight onto its face and the relief size that fits (in a project with a gate set `PARAMS` first, or `relief()` cuts by the plugin default 2.45 and not by the project's `inserts.relief_wall_min`); put it in the same gate script as `wall()` and count those rows. A check that only reports (`audit()`'s `no_relief`) gets skipped: a part with 12 pockets and no relief was printed and did not seat on its neighbour. After cutting reliefs run `wall()` again: `relief()` deepens the lead-in by the relief depth, and where a pocket from the opposite face stands close the wall between the two lead-ins shrinks; there cut the relief alone.
- **PLA hides a loose pocket.** PLA prints holes small, so a pocket that is fine in a PLA test can be loose in a CF filament. Test the pocket in the filament the real part uses.
- **Test coupon first.** Before changing every pocket in a project, print a small block with 4 to 7 pockets of different sizes and the hole size engraved by each (about 10 g, 25 minutes). The right one lets a cold insert drop only the pilot band's depth, then needs heat; after install, try to pull the insert out by hand with a screw in it.
- **Install** with a stop: a screw and a flat washer on the insert, so it ends flush. To rescue a loose insert, use 2-part epoxy with the same stop and a greased screw, not superglue: it is brittle and wicks into the thread.
- **Changing pockets that already exist** (`convert`): add a sleeve (outer Ø = old pocket, inner Ø = new hole) from the lead-in floor to the pocket floor and join it at the end of the timeline, one part at a time. Do not edit old features. Afterwards check every pocket: the new diameter's length, the lead-in's length, and that nothing solid is left on the axis (a short core leaves a thin plug). On a long hole with inserts at both ends, treat each end as its own pocket.

## Parts that mate

- Derive a pocket's depth from the thickness of the part that sits in it; never type the number twice. A pocket a few tenths too shallow leaves that part standing proud.
- Two parts that lie on each other take their outline from one function, so a change to one moves both.
- An insert longer than the material it sits in pokes through the other face. Check insert length against floor thickness, and thicken the floor (and what sits on it) if needed.
- **Choose each screw length, then check both ends.** Take a length that is sold (for M3: 6, 8, 10, 12, 14, 16, 20, 25 mm), then check that the tip ends inside the insert or nut and short of the pocket floor, and that the head sits 0.5–2 mm below the surface. If no stocked length fits, deepen the counterbore or fill under the head, rather than leaving the head proud or the tip on the floor. Count the thread engaged: a screw that grips only 2–3 mm of a 4 mm insert pulls out under load.
- **Every screw must be reachable.** A screw goes in along its axis and the hex key follows it: nothing printed may sit in that straight path, not even a thin ledge over the edge of a head seat. Run `tools/fusion/screw_access.py` `access(doc)` after every change near screws; each hit names the part and the distance from the head. A hit by a part mounted later is fine only when the assembly order says so; name it to the user.
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
3. Write the results in `DESIGN_RULES.md` tagged **[M]**. Nothing is [M] until a real print says so. In a gated project a measured limit also goes into the gate, or the gate keeps judging with the old number: propose it to the owner, and after his word `python3 gate/tools/gate.py owner param NAME VALUE --tag M --source "what was printed and measured" --quote "his words"`.
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

## Registered rules

The rules below are generated from the plugin's rule registry (`rules/registry/`), with the default numbers. A lesson is added there, not typed here. `block` rules are checked by the gate and stop the stamp of a part; `attest` rules are questions the gate asks; `advice` rules have no check yet and are applied by hand. Where a hand-written paragraph above and a rule below differ, the rule is right.

### Inserts

<!-- gate:rules inserts start -->
<!-- Generated from rules/registry/inserts.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **Plastic under a blind pocket** (`inserts.floor`, block at design). Under the floor of a blind pocket at least 0.6 mm of plastic stays, or the pocket is through on purpose. An insert is never longer than the material it sits in.
  - Fix, in this order:
    1. Make the part thicker under the pocket, and what sits on it.
    2. Use a shorter insert with its own table row.
    3. Cut the pocket through on purpose.
- **The iron reaches every pocket mouth** (`inserts.mouth_reachable`, block at design). In front of every pocket mouth a straight path 8.0 mm wide and 30.0 mm long is free of the part itself. An insert is set on the bench, in the bare part: a neighbour that is mounted later does not count. A through hole with a pocket at each end has two mouths; a straight through pocket takes its insert from either end and fails only when the part closes both. No pocket is sealed. Prefer a face that is a top face in the print.
  - Fix, in this order:
    1. Turn the pocket to a face that is open.
    2. Open a window in what covers the mouth.
    3. A neighbour in front of the mouth is no finding of this rule: write into the build notes that the insert is set before that part is mounted.
- **Every heat-set pocket has a known profile** (`inserts.pocket_shape`, block at design). A hole whose diameters fall in the band of an insert kind of rules/tables/inserts.json has exactly the pocket profile of that kind: melt relief, lead-in, hole, depth. The profile is cut by the shared pocket tool from the table, never typed into a part script. A pocket shortened for a thin part is a named exception, not a second profile.
  - Fix, in this order:
    1. Cut the pocket again from the table with the shared pocket tool, together with its relief.
    2. A kind with no measured row: print a coupon in the final filament first (inserts.coupon_first), then add the row.
    3. If the hole is not an insert pocket, answer the question "what is this hole" once.
- **Every pocket has its melt relief** (`inserts.relief_missing`, block at design). Every heat-set pocket is cut together with a melt relief at its mouth: a step of a relief diameter of the table, 0.4 mm to 0.6 mm deep (whole layers), on the face the mating part lands on, wherever a relief diameter of the table leaves 2.45 mm beside it. The insert ends flush with the relief floor. A pocket with no relief is a row on every face, the bed face included. A pocket at the bottom of a recess (a spot face, a counterbore) needs its relief in the floor of that recess: the recess is not the relief.
  - Fix, in this order:
    1. Cut the relief with the shared pocket tool. A new pocket is never cut "relief later".
    2. Run the wall checks again: the lead-in moved down by the relief depth, and a pocket from the opposite face may now be too close.
    3. No relief diameter of the table leaves the wall: the row goes to the owner as one decision for the group. He chooses a smaller relief with less beside it, or no relief with the bench line "shave the melt ring" (inserts.noroom_bench).
- **Plastic round a heat-set pocket** (`inserts.wall`, block at design). Round every heat-set pocket the plastic is at least 4.0 mm wherever the part has room, at the lead-in and along the insert. The wall is measured to the first way out of the plastic: an outside face, another pocket, a hole or a cavity, also one cut from the opposite face. Less is allowed only where a named neighbour stops the wall, with the owner's waiver, and never under 1.6 mm. Every pocket under the target is a row: FAIL under the floor, GROW with free space, BLOCKED by a named neighbour. There is no pass value below the target.
  - Fix, in this order:
    1. Where the outline of the part is free on that side, make the whole wall, shoulder or lap wider: one flat face, no islands.
    2. Move the pocket together with its screw hole in the mating part and its hardware refs.
    3. A local boss only where the outline cannot move, with the reason named.
    4. Blocked on every side: report it with the neighbour that stops the wall and ask the owner for a waiver.
- **Plastic beside the melt relief** (`inserts.wall_at_relief`, block at design). Beside the melt relief, at the relief depth, at least 2.45 mm of plastic stays. Where a neighbour stops the wall, the relief steps down through the fallback diameters of the table before the wall gets thinner than that.
  - Fix, in this order:
    1. Use the next smaller relief diameter of the table.
    2. Where the outline of the part is free on that side, make the whole wall, shoulder or lap wider: one flat face, no islands.
    3. Move the pocket together with its screw hole in the mating part and its hardware refs.
    4. A local boss only where the outline cannot move, with the reason named.
    5. Blocked on every side: report it with the neighbour that stops the wall and ask the owner for a waiver.

Asked by the gate. Each is answered with evidence, never with a typed number.

- **No load-bearing thread cut in the print** (`inserts.no_tapped_plastic`, attest at design, once per finding). A screw that is tightened against a load or adjusted again goes into a heat-set insert, a nut or a bought thread, never into a thread cut or formed in the print. A thread-forming pilot is allowed only for a rarely turned, lightly loaded screw, as a named exception.
  - Question: "Hole {feature}: a thread in the print. Which screw goes in, and why not an insert?"

Advice. No check exists yet: apply it by hand.

- **A pocket size is trusted only after its coupon** (`inserts.coupon_first`, advice). Before a pocket size goes into parts, print a coupon in the final filament and nozzle: a small block with 4 to 7 pockets, the hole size engraved by each (about 10 g, 25 minutes). The right pocket lets a cold insert drop only as deep as its pilot band and then needs heat. After install, pull on the insert by hand with a screw in it. A result in one material does not carry to another: PLA prints holes small and hides a loose pocket.
- **Inserts are installed against a stop** (`inserts.install`, advice). An insert is melted in against a stop: a screw and a flat washer on the insert, so it ends flush with the relief floor. Inserts go in before a bearing is pressed in beside them. A loose insert is rescued with two-part epoxy, the same stop and a greased screw, not with superglue: it is brittle and wicks into the thread. A printed part that fails the wall rule gets a written work-around for the bench: melt its inserts last and briefly.
- **A pocket with no room for a relief is shaved at the bench** (`inserts.noroom_bench`, advice). A pocket where no relief diameter of the table leaves 2.45 mm, typically a clamp-washer pocket close to a bearing seat, keeps no relief only by the owner's decision. The melt ring it leaves is shaved flush with a countersink or a knife after install, and that step is written in the build notes of the part. Where a washer heels on the same landing, the ring is gone before the washer goes on.
- **One pocket table for every project** (`inserts.pocket_table`, advice). Pocket sizes come from one shared table keyed by insert kind (rules/tables/inserts.json). A project uses them and does not copy the numbers. Each fastener-in-plastic kind a project uses (every insert size, a thread-forming pilot, a wood screw) has its own row: hole, depth, relief. A kind whose row is not measured is not used in a part until its coupon is printed, and a project changes a row only when a print in its own filament and nozzle measured otherwise.
- **A moved face takes its pockets with it** (`inserts.recut_after_face_move`, advice). Pocket depth is measured from its face. When a face with pockets is moved, filled, offset or cut (a clamp landing, a spot face, a relief), the pockets and their reliefs are cut again from the new face in the same pass, the wall checks are run again and the insert refs are moved with them.
- **The relief is sized by the install stop** (`inserts.relief_stop_washer`, advice). The relief diameter is the stop washer of the install plus clearance. The full relief takes a normal flat washer, the first fallback a small-series washer, and the last one has no stop: the insert is pushed 0.5 below the face by eye. The table lists the stop of each diameter. The relief is not deeper than 0.6 mm: every extra millimetre costs thread engagement at stock screw lengths.
- **Reserve pockets on a long print** (`inserts.reserve_pockets`, advice). A long, expensive print may carry reserve insert pockets for a likely future interface where they cost nothing. They follow every pocket rule, and inserts that stay unused are named as such in the hardware refs.
- **The project names its insert make** (`inserts.type_named`, advice). The pocket table is valid for one insert make and shape: "brass, spiral knurl, smooth pilot collar on top". The project names its make, and every purchase is checked against it. A straight or diamond knurl of the same nominal size needs its own coupon.

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category inserts`, or `gate/RULES.md` in a gated project.

<!-- gate:rules inserts end -->

### Screws

<!-- gate:rules screws start -->
<!-- Generated from rules/registry/screws.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **A screw reaches far enough into its thread** (`screws.engagement`, block at design). Every screw is engaged over at least 3.2 mm (M2 2.0, M2.5 2.5, M3 3.2, M4 4.0, M5 5.0, M6 6.0) (per thread size) in its insert, nut or tapped part. Its length comes from the stack: plastic under the head, the parts between, the engagement, ending short of the hole end. A change of length moves everything stacked on the screw: length and seat change together. A screw whose thread nothing receives (no insert, no nut, no hole narrower than the thread anywhere along it) is a finding with 0 mm: the row names the hole and the part its thread ends in and what lies on that axis. A length found along a line by sampling meets the limit on the 0.05 mm measurement grid (a drawn 5.0 read as 4.99 is 5.0; a real 4.9 stays 4.9).
  - Fix, in this order:
    1. Pick the next stock length.
    2. Shorten the stack under the head.
    3. Never reach the thread by a deeper counterbore.
    4. Nothing receives the thread: model the insert or nut as a ref body, or name the body that is there so that its ref pattern matches.
- **The whole screw path is open** (`screws.hole_through`, block at design). For every screw the path head seat, clearance hole, thread is open: no more than 0.1 mm of the shank lies in solid printed plastic. Holes are cut through-all or 1.0 mm past the face, never to a typed depth that a later growth of the body can close. Holes and pockets are checked again after every join or fill. One skin is meant: the sacrificial bridge right under a head whose counterbore prints facing the bed, up to one bridge step (0.3 mm) thick. It is told with its bench step and does not fail here; whether it is a bridge layer or a leftover skin is asked once, by access.hole_closed. The same skin anywhere else along the screw fails.
  - Fix, in this order:
    1. Cut the hole again through everything the screw passes, through-all or past the face.
    2. Find the join or fill that closed it and move it down the timeline, or cut after it.
- **A screw clamps plastic, not air** (`screws.shell_bears`, block at design). A screw never clamps across an air gap through a thin wall. A local pad closes the gap to 0.05 mm so the screw clamps plastic in compression, and the wall round the screw is at least 3.0 mm. The open gap of a bearing clamp is the one exception: there the screw closes on the ring. That holds for a clamp washer on a ring, and for a gap between two parts that a bought solid body bridges (a bearing ring the parts clamp between them): it stands in the gap within 8.0 mm of the screw axis and both parts press on it along the screw, each within 0.05 mm. A gap with nothing in it stays a finding, and so does a body that only one part presses on: a ring in a bore with no shoulder behind it, a pin through both parts, a part that stops short of the body.
  - Fix, in this order:
    1. Add a local pad that closes the gap, so the screw clamps plastic in compression.
    2. Thicken the wall round the screw.
    3. A gap that is the clamp room of a ring: model the ring as a bought body in the gap, with both parts landing on it; the screw unit then finds it.
    4. A gap that must stay open: owner waiver that names the screw and what it closes on.
- **A screw does not bottom** (`screws.tip_bottoming`, block at design). The tip of every screw stops at least 0.5 mm before the end of its hole, pocket or bought thread. A length found along a line by sampling meets the limit on the 0.05 mm measurement grid (a drawn 5.0 read as 4.99 is 5.0; a real 4.9 stays 4.9).
  - Fix, in this order:
    1. Pick the next shorter stock length.
    2. Deepen the tip hole below the insert.
- **Plastic under every screw head** (`screws.under_head`, block at design). Under every screw head at least 5.0 mm of plastic stays, measured along the screw. A plate too thin for it is made thicker; neighbours of the project's own design move before the plate is thinned. A head that bears through its washer on a bought rigid body and on no plastic (a clamp washer on a bearing ring, within 0.05 mm) crushes no plastic: it is judged as a clamp, not here. A length found along a line by sampling meets the limit on the 0.05 mm measurement grid (a drawn 5.0 read as 4.99 is 5.0; a real 4.9 stays 4.9).
  - Fix, in this order:
    1. Make the plate thicker.
    2. Use a shallower head recess and a longer stock screw.
    3. A head that must stay deep because the space above it is swept: an owner waiver with the reason.

Asked by the gate. Each is answered with evidence, never with a typed number.

- **Enough screws on a joint that carries load** (`screws.count_load_joint`, attest at design, once per changed part). A joint that carries torque or tipping has at least 4 screws, on the largest bolt circle the parts allow; the count comes from the load on the farthest screw. A clamp plate in the load path keeps its thickness and its screw count: neighbours of the project's own design move before the plate is thinned or a screw is dropped.
  - Question: "Does this part sit in a joint that carries torque or tipping, and how many screws hold it?"
- **One screw family, in stock lengths** (`screws.family_lengths`, attest at design, once per changed part, deferred: registered, not asked or run yet). A project uses one screw family and drive ("hex socket: cap DIN 912, button ISO 7380, countersunk DIN 7991") in the lengths its owner stocks (rules/tables/screws.json, stock_lengths). The length that reaches the thread is chosen; no long through-bolts. A length outside the stock list is flagged before the design depends on it.
- **A counterbore is only as deep as the head** (`screws.head_recess`, attest at design, once per changed part, deferred: registered, not asked or run yet). A counterbore is the head height plus 0.5 mm to 2.0 mm. The thread is reached by the screw length, never by a deep counterbore. A head is not raised into a zone that a moving part sweeps.
- **A head bears on a full ring** (`screws.head_seat_full_ring`, attest at design, once per changed part, deferred: registered, not asked or run yet). A head bears on a full ring of plastic: hole and seat lie fully inside material, with a margin to the edge. A counterbore near an edge becomes a U-slot or gets a boss. Heads of neighbouring screws do not overlap, and no screw ends in air.
- **A clamp that goes on through a bearing is meant** (`screws.load_through_bearing`, attest at design, once per finding). Screws may clamp across an open gap between two parts when one part presses a bearing ring and the load goes on through the bearing to the other part: through its balls to the other ring, and on through what stands against that ring (a spacer, a second bearing) to the other part, so the screws preload a bearing stack. The gate measures only the bodies at the screw: it sees the ring one part presses and the other part standing off it, and asks.
  - Question: "The screws of this part clamp across an open gap; {by} is pressed by one of the two parts, and the other stands off it (the finding says by how much). Does the load go on from that bearing, through its balls and its other ring and what stands against that ring, to the other part, so that the screws preload a bearing stack?"
  - Fix, in this order:
    1. A stack that is meant: write the load path into the build notes and answer the question with it.
    2. No such path: let the part that stands off land on the ring, or close the gap (screws.shell_bears).

Advice. No check exists yet: apply it by hand.

- **The thread of a bought part is read, not assumed** (`screws.bought_thread_read`, advice). The thread size of a bought part is read from its model and measured on the part (a D4.0 hole means M4), never assumed. Clearance hole and counterbore then come from the table row of that size.
- **A friction clamp needs two screws or a shape lock** (`screws.clamp_two_or_shape`, advice). A friction clamp on a rotation axis has at least 2 screws off the axis, or a shape lock: flats, a key, teeth. A clamp longer than one screw pitch has screws along its length. A slit clamp is slit through everything that would hold it open. An adjusting screw whose load reverses is captured both ways.
- **Stacked parts share one screw set** (`screws.one_screw_set`, advice). Parts that lie in series on one bolt circle share one screw set: the screw goes through all of them into the inserts of the last part, its head on the outer part with at least 5.0 mm under it, in a stock length. The parts stay separate. Before adding inserts for a new part, look for a screw set that already passes there.
- **Torque and thread locker are written down** (`screws.torque_locker`, advice). The assembly notes give a torque for screws into inserts: "0.25 to 0.3 N*m for M3 into a brass insert, never over 0.4". Clamp screws get low-strength thread locker. No anaerobic locker on a screw threaded into printed plastic. Under heads on nylon use a washer or a wide head and tighten again after the first days: the plastic creeps.

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category screws`, or `gate/RULES.md` in a gated project.

<!-- gate:rules screws end -->

### Washers

<!-- gate:rules washers start -->
<!-- Generated from rules/registry/washers.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **A washer lies on its landing** (`washers.landing`, block at design). Every washer lies on material over at least 95 % of its ring, within 0.05 mm, in its real seated position and at its measured outer diameter. The ring is counted outside the opening the washer sits over (its screw hole, a pocket mouth with its relief). A washer over a slot of the part bears on the two flanks of the slot: it passes when it reaches at least 1.0 mm over each flank and its ring is on material everywhere beside the slot. It fails when a flank is missing, and when the slot is nearly as wide as the washer. A spot face is at least 1.2 mm wider than the washer. A clamp washer on a bearing ring is the one washer that bridges: it bears on its ring, and everything else under it stays at least 1.0 mm below its face.
  - Fix, in this order:
    1. Widen the spot face.
    2. Move the screw so the whole ring lies on the landing.
    3. Use the washer size of the table that fits.
    4. A clamp washer: set the landing beside the ring back by the clamp room.
- **A washer and its head clear the neighbours** (`washers.proud`, block at design). A washer with its screw head keeps at least 0.5 mm to every body it does not clamp. The stack stands proud by the washer thickness plus the head height: 2.65 for a 1.0 washer under an M3 button head, 3.15 for a 1.5 washer. Where a fixed part is nearer than that, washer and head are sunk flush or the stationary part gets a groove.
  - Fix, in this order:
    1. Counterbore the washer and the head flush.
    2. Groove the stationary part.
    3. Use a flat-sided or smaller washer.
    4. Move the screw.

Advice. No check exists yet: apply it by hand.

- **A clamp washer touches only its own ring** (`washers.clamp_geometry`, advice). A clamp washer overlaps its own bearing ring by about 2.0 mm and touches nothing else: not the other ring, not the seal, no fixed part. It is modelled at its measured outer diameter, in its real seated position, and checked like a part. Its spot face is wider than the washer, and the floor of the spot face is set back from the ring face by the clamp room.
- **A washer landing is free of melt rings** (`washers.landing_melt_ring`, advice). A washer that heels on a landing with insert pockets needs that landing flat. Every pocket there has its melt relief, or its melt ring is shaved flush before the washer goes on, as a written bench step.
- **A head over a slot sits on a washer** (`washers.over_slots`, advice). A screw head over a slot or an oversize hole sits on a flat washer that spans it. A bare head bears on the two flanks of the slot only.
- **Washer thickness follows the load** (`washers.thickness_by_load`, advice). Where a washer clamp carries tipping, the washers are at least 1.5 mm thick or hardened. The thicker washer stands 0.5 more proud: check washers.proud again.
- **Printed washers and shims need their own slicer settings** (`washers.thin_printed_rings`, advice). A printed washer, shim or ring narrower than two line widths (0.42 mm each) is sliced with the Arachne wall generator and without X-Y hole compensation, set per object. Its thickness is a whole number of layers. Where that cannot be had, use a metal shim.

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category washers`, or `gate/RULES.md` in a gated project.

<!-- gate:rules washers end -->

### Bearings

<!-- gate:rules bearings start -->
<!-- Generated from rules/registry/bearings.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **A clamped ring has clamp room** (`bearings.clamp_room`, block at design). The axial room between the two stops of a clamped ring is shorter than the ring by at least 1.0 mm: the screws close on the ring, not on the plastic, and a visible gap stays open at the flange. The same holds for a spacer clamped between two rings. Clamp screws sit next to the ring. The open gap is counted in every stack above it. A ring is clamped when two bodies touch its two faces (each within 0.05 mm); a body that only stands near a face clamps nothing, and bearings.holding fails that face.
  - Fix, in this order:
    1. Set the landing of the clamp (washer landing or cover face) back from the ring face by the clamp room.
    2. A spacer clamped between two rings: make the room for it shorter than the spacer by the clamp room too.
    3. Add the open gap to every stack above it: screw lengths and the height of whatever sits on the flange.
    4. Run the insert and relief checks of the part again: a landing cut can remove a melt relief or thin a wall, and it moves the hardware refs.
    5. A part that is already printed: a shim of the missing room under the clamp, recorded as a bench patch; the model is fixed all the same.
- **Every bearing ring is held on both faces** (`bearings.holding`, block at design). Bearings are found, not listed. A name picks the candidates (the bearing pattern, or a designation of the bearing table in the name of a body or of its component); the geometry decides: a ring whose d, D and B are a row of the table is that bearing, and a body that measures otherwise than the row its name carries is not a bearing. A ring that only misses its row (two of d, D and B fit: the model of the bought part is off), and a ring the probe passed over, is not judged and not passed: the check is not run for it until the model or the table is corrected. Each ring of each bearing (inner and outer) is held along the axis on both faces, four contacts per bearing, by something that touches only that ring: a printed shoulder or lip over at least 50 % of the ring-face circle, or at least 4 separate clamps (steel washers under screws) round it, each within 0.05 mm of the face. A press fit alone holds no ring. A cover that touches both rings fails; the other bearing of a pair, ring on ring, is no such cover. A bearing with no table row or no ring geometry is never skipped: the check is not run until the row is added or the ref is a ring. A ring is judged with the part that seats it or holds one of its faces. A bearing that bought bodies hold and at whose rings no printed part stands, seats or clamps (the rollers inside a bought gearbox) is not a finding of a printed part that is screwed to that assembly: one row per part tells how many and which, and names the mesh bodies near them. A printed lip or clamp that stops short of a ring face answers for that face, however far off it stands. A bearing that nothing touches at all stays a finding of every part near it. A ring that is free on purpose is a named exception: an owner waiver on this rule.
  - Fix, in this order:
    1. Add a printed shoulder or lip on the open face: on that ring only, past the ring chamfer (the bearing table gives the least shoulder diameters).
    2. Where a shoulder cannot be printed or assembled, add steel washers under screws next to the ring, spread round the circle.
    3. A cover that touches both rings: relieve it over the other ring and the seal.
    4. A bearing with no table row: add its size to the bearing table. A bearing the gate did not find at all: name its ref so the pattern matches.
    5. A ring that is free on purpose (a floating bearing against one lip, an idler pair on its bolt): owner waiver that names the bearing and what closes it.
- **A seat fits its bearing** (`bearings.seat`, block at design). Every bearing sits in a seat of its own size: the seat diameter where the ring ends up differs from the bearing diameter (bore for a seat on a post, outer diameter for a seat in a housing) by at most 0.3 mm, or the seat is a tapered press fit: a post nominal (within that tolerance) at the end the ring enters and at most 0.5 mm wider towards the shoulder that stops it, a bore nominal at its stop and at most 0.25 mm wider towards its mouth, measured end by end; the way into the seat is not in the bearing's way by more than that; and every bearing-sized seat of the part holds a bearing. Sizes come from the bearing table; a size or formula copied from another bearing is evaluated again. Every bought bearing exists as a named ref body in each document that uses it. A seat with a bought body on its axis that no pattern recognised is not called empty: the check is not run until the body is named. A hole that is only near a bearing diameter is asked, not failed.
  - Fix, in this order:
    1. Take d, D and B from the bearing table of the rules (tables/bearings.json).
    2. Evaluate again every size that was copied from another bearing.
    3. An entry that is in the way: widen everything the bearing passes to its own diameter and more (bearings.insertion_path).
    4. A seat with no bearing in it: place the bearing ref (in every document that uses the bearing), or remove the seat.
    5. A seat that is tapered or compensated beyond the taper parameters on purpose: the project raises them with a measured or owner value, never the agent.

Asked by the gate. Each is answered with evidence, never with a typed number.

- **A bearing can reach its seat** (`bearings.insertion_path`, attest at design, once per changed part, deferred: registered, not asked or run yet). Everything a bearing passes on the way to its seat clears it by 0.2 mm on the radius: larger than its outer diameter in a housing, smaller than its bore on a post. That includes a ring, a lip or the seat of another bearing that lies on the way. A formula copied from another bearing size is evaluated again.
- **No printed plastic in a bearing clamp stack** (`bearings.no_plastic_in_clamp`, attest at design, once per changed part). Between the head of a clamp screw and the ring it clamps there is steel only: the washer and the ring. At most 0.0 mm of printed plastic lies in that stack: plastic under a clamp load creeps and the clamp loosens. A longer standard screw is no reason to put plastic into the stack.
  - Question: "For every bearing clamp of this part: what lies between the screw head and the ring?"
  - Fix, in this order:
    1. Seat the washer on the ring and the screw head on the washer.
    2. Thread the clamp screw into the part the ring sits in, next to the ring, so nothing printed lies under the head.
    3. Plastic that must stay in the stack: owner waiver with its thickness, and a retighten step in the build notes.

Advice. No check exists yet: apply it by hand.

- **A bearing that is not clamped is a named exception** (`bearings.exceptions_named`, advice). A bearing that is not held on four ring faces is an exception with a name: a floating bearing against one lip, an idler pair on its bolt, a bearing captured in the print, the bearings inside a bought unit or inside a sub-assembly that has its own document. Each one has an owner waiver on bearings.holding that names the bearing, what holds it instead and why that is enough. An exception that stands only in a comment of a tool is not named.
- **One stack sets a bearing pair** (`bearings.one_stack_sets_pair`, advice). In an arrangement of two bearings exactly one stack sets the length, for example the inner rings through a spacer. The other ring pair has no second hard stop: it keeps an open gap of 0.3 mm or slides. A part has one axial datum. Where two load paths meet across a bearing, one of them is a sliding fit with an open gap sized from the measured bought part.
- **A bearing captured in the print** (`bearings.print_in`, advice). A bearing that is laid into a paused print (print-in): layers do not stick to steel, so the layer over the bearing needs plastic beside it to land on; the pause height lies on the layer grid; two parts whose lips face each other cannot be assembled round a captured bearing. Prefer a bearing that slides on after printing.
- **A seat prints as a cylinder that opens upward** (`bearings.seat_prints_upward`, advice). A bearing seat prints as a vertical cylinder that opens upward, with no support inside it. If the layers are then loaded badly, keep the seat orientation and add material.
- **A printed seat is tapered, and shrinks beside a thick base** (`bearings.seat_taper`, advice). A printed bearing seat is tapered with a lead-in: nominal where the ring ends up, 0.25 mm wider on the diameter at the entry (a post: smaller at its free end), with a 0.3 mm x 45 degree chamfer at the mouth. The part of a seat right beside a thick base shrinks more as it cools: compensate it by 0.5 mm on the diameter until the first print is measured, then use the measured value. Zones another ring must pass stay clear of the taper. A compensation above 0.3 mm needs that tolerance raised in the project with a measured or owner value.
- **A bought shaft through a bearing carries a printed sleeve** (`bearings.shaft_sleeve`, advice). A bought shaft or pin through a bearing is 2.0 mm smaller than the bore and carries a printed sleeve with 0.2 mm of axial play. A shaft of the nominal bore size is bought only after its tolerance class is read: an m6 pin does not enter a bore of the same nominal size. Its length is checked against the pocket measured on the exported part, and only ready lengths are bought. The sleeve is a one-line wall: its two diameters come from a fit ladder (walls.one_line).
- **A shoulder touches one ring** (`bearings.shoulder_one_ring`, advice). A shoulder or hub beside a bearing touches exactly one ring. Its band lies on that ring's face, past the ring chamfer (the bearing table gives the least shoulder diameters), and it stands back from the other ring and the seal by 0.5 mm along the axis. A lip that holds a ring is at least 2.0 mm thick and follows the minimum wall; where that does not fit, pick a narrower bearing. A flat ledge that faces down in the print is not a shoulder: it cannot be printed.
- **A sleeve end takes no clamp load** (`bearings.sleeve_end_and_preload`, advice). The end of a printed sleeve or thin spacer never takes the clamp load of a bearing: the clamp closes on steel. Preload screws are tightened evenly and only until the play is gone. A ring is pressed home only through the ring that is being fitted, never through the balls.

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category bearings`, or `gate/RULES.md` in a gated project.

<!-- gate:rules bearings end -->

### Walls and thin features

<!-- gate:rules walls start -->
<!-- Generated from rules/registry/walls.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **No wall thinner than the minimum** (`walls.min_material`, block at export). No wall, strap or neck of the exported STL is thinner than 1.5 mm: the larger of 1.5 mm and two lines of the target project (one line is 0.42 mm). It is measured as material between faces on the STL. A wall under one line does not print at all. Rims round pockets, reliefs and holes count: no hit is hidden for being small. A neck is always an error. A thin sheet is fixed, or is a named exception with its reason and load case. A result that sampled less than 90 % of the surface is not proven and is not a pass.
  - Fix, in this order:
    1. Make the wall thicker: grow it into the free space, up to the real neighbour.
    2. Move or shrink the feature that thins it: the hole, pocket, relief or notch.
    3. Remove a thin feature that has no function.
    4. A thin sheet that must stay (a shim, a bridge layer): owner waiver with the reason and the load case. A neck gets no waiver without a load case.
    5. Not proven: run the wall check again with a longer budget; a partial result exports nothing.
- **A wall between one and two lines is a decision** (`walls.one_line`, block at export). A wall between one and two line widths of the target project (one line is 0.42 mm) is a decision, not a default. Classic walls lay two full lines into it and the bore inside closes. It prints only with a thin-wall generator (Arachne) set on that object and checked in the sliced path, and its size comes from a printed fit ladder, not from the model: a one-line tube prints smaller outside than it is sliced.
  - Fix, in this order:
    1. Make the wall two lines or more.
    2. Keep it as one line: thin-wall generator on that object, checked in the sliced path.
    3. Take both diameters from a printed fit ladder (walls.one_line_ladder).
    4. Owner waiver that names the ladder and the sizes that fitted.
- **No air gap the slicer will close** (`walls.slit`, block at export). No air gap between two walls of the STL is narrower than 0.3 mm: the slicer closes or bridges it. A slit that has to open or close in use (a clamp slit) is far wider, never under one line of the target project, and is looked at in the sliced path.
  - Fix, in this order:
    1. Widen the gap to a width the slicer keeps open.
    2. Remove the gap: join the two walls.
    3. A gap that must stay this narrow: owner waiver, after a look at the sliced path.
- **No sliver faces** (`walls.thin_faces`, block at design). No planar face of a printed part is a leftover narrower than 1.2 mm: a sliver, a narrow ledge, the crescent left by an old hole, the land of a rib that is too thin. Old holes are filled before new ones are cut. Cuts and fills end on existing faces. Features never meet on an edge only: the body is one lump, and no edge joins more than two faces. A narrow face is judged by how it joins its neighbours, not by its width alone. One flat of a chamfer, a bevel or a faceted curve (a tooth flank) is no leftover: each of its long sides turns by at most 60° deg, and the sides that turn by more than half of that turn the same way. The bridge steps of 0.3 mm between a counterbore and its hole are a print aid and no leftover. Narrow faces that touch are one finding. Equal places that repeat round one axis (the teeth of a wheel, a spline, a knurl: at least 12 places with the same faces and widths, on the steps of one even division of one circle) are one finding for the profile, with the count, the axis and one place; a place that differs keeps a row of its own. How thick the material is behind a face is not judged here: the wall check on the STL measures it (walls.min_material, walls.one_line). The check runs on every discovered part of every document, not on a typed list.
  - Fix, in this order:
    1. Fill the old hole completely before the new one is cut.
    2. End the cut or fill on an existing face; move it so that no strip is left.
    3. Remove a ledge that has no function.
    4. Two volumes that meet on an edge only: let them overlap, or move them apart.
    5. An intended thin face that the check does not know as a strip or as bridge steps: owner waiver for that place; everything else reaches zero.

Advice. No check exists yet: apply it by hand.

- **A load-carrying part is grown to its neighbour** (`walls.grow_into_free_space`, advice). A load-carrying part is grown to its real limiting neighbour instead of being sized to a minimum: where the space exists a structural wall is 8.0 mm; a wall in a load path is at least 3.0 mm; no load path runs through a thin bridge. Clearance cuts follow the real envelope of the moving part. Thin open plates are closed into continuous walls with full-height ribs.
- **Geometry that lost its function is removed** (`walls.leftovers`, advice). Geometry that lost its function is removed in the pass that orphaned it, on printed parts too. A fill ends exactly on the intended surface: no dent, seam or witness mark. A dent or pocket shallower than 0.3 mm and a step lower than 0.5 mm are leftovers, not features.
- **Feature sizes follow the nozzle of the target project** (`walls.nozzle_min_feature`, advice). Minimum feature sizes follow the nozzle (0.4 mm) and line width (0.42 mm) of the project the part is placed in. No chamfer, slit, land or rib is narrower than one line. A thin feature is a whole number of lines or layers. A thin horizontal sheet has at least 4 layers. Small text has strokes at least one line wide. A part with features finer than the nozzle prints goes to a project with a finer nozzle; it is not left in the coarse one.
- **The size of a one-line wall comes from a fit ladder** (`walls.one_line_ladder`, advice). A tube whose wall is one extrusion line prints smaller outside than it is sliced, and its bore is not the model's either. Both diameters come from a printed fit ladder: one row per dimension in steps of 0.05, the pieces told apart by height. The rows are not independent: the bore row is printed at the outer diameter that won the first row and is valid only near it. A size is measured only when the owner has named the piece that fits; until then it is decided.

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category walls`, or `gate/RULES.md` in a gated project.

<!-- gate:rules walls end -->

### Access: holes, heads and mouths stay reachable

<!-- gate:rules access start -->
<!-- Generated from rules/registry/access.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **A hole is open end to end** (`access.hole_closed`, block at design). A hole does not end in a skin of material up to 2.0 mm thick where its stack, a coaxial hole or a screw shank says it goes on. After any join, fill, fillet or growth of the body every hole and pocket of the zone is checked again along its axis. Solids first, cuts last. A fillet must not eat a functional feature. One exception is asked, never passed in silence: a bridge layer, a skin of at most 0.3 mm between a counterbore that faces the bed and its narrower hole, which is opened after the print. A counterbore that does not face the bed (flatter than 45°) needs none: there the skin is a finding.
  - Fix, in this order:
    1. Cut the hole through all, or 1 mm past the far face; never to a typed depth.
    2. Cut holes after the last join or fill of the zone.
    3. Run the gate again after every join, fill or fillet: the hole census of the part must not lose a hole or an open end.
- **What an opening is for stays reachable** (`access.hole_covered`, block at design). What must be free in front of an opening depends on what the opening is for. A screw hole: the key side, a straight path of the hole's own section for 60.0 mm; of a straight through hole with no screw ref the side that is better off counts, and a hole that holds a screw ref is judged at the head of that screw, on the part the head sits on (a screw is known by its shape first, so a head body and a shank body under any name are one screw). A bearing seat: the side the bearing goes in from, for the width of the bearing, free of the part itself. A slot or window with nothing in it: like a key side. A hole of no table is asked about once (what is it for), an insert pocket has its own rule. Holes on one axis that follow each other within 2.0 mm are one bore, judged at its outer ends. What sits in an opening by design does not cover it: the fastener and the mating hole on its axis, the bearing in its seat, a bought part or a fastener that reaches into a slot or window (a lock screw in its slot, a motor in its window; a mesh or a printed neighbour inside it is judged like a cover; the rim of the part itself within 0.2 mm of such a mouth is no cover either). The path is tested against every body in reach, hidden ones too. The part itself in the path is a finding. Other solid bodies in the paths of a part are ONE row and one question for the part: every covering part nearest first, with its distance and the openings it stands in front of; free only if each is mounted later and comes off first. A body inside a bought or linked assembly is named with the assembly it belongs to. A mesh has no exact shape: it is never a finding, the check stays open until the mesh is a solid body or the owner names it an overlay (the parameter access.overlay_pattern). A hit under 2 % of the section is noise.
  - Fix, in this order:
    1. The part itself in the path: cut it back to the full section of the opening, over the whole length.
    2. Another part in the path that is mounted later and comes off first: answer the assembly-order question and write the order into the build notes.
    3. Another part that is there first: cut it back, move it, or move the opening together with what mates with it.
    4. A mesh in the path: make it a solid body. When it is no part of the build, ask the owner to name it in the overlay pattern.
    5. An opening that lost its function: remove it. A reserve insert under another part is removed or named unusable.
- **No mouth is sealed** (`access.mouth_sealed`, block at design). No pocket or hole is closed over its whole section, at every end it has, by a body nearer than 0.2 mm, unless that body is the fastener or the mating hole that belongs there. A blind pocket has one end. A through hole that lies against a plate at one end still takes its pin or insert from the other; holes on one axis within 2.0 mm of each other are one bore. A plain screw clearance hole needs both ends, one for the key and one for the thread behind it: one sealed end closes it. A sealed hole takes no screw, insert, pin or cable, whatever the assembly order. A body the owner named an overlay (access.overlay_pattern) seals nothing.
  - Fix, in this order:
    1. Open the mouth: cut the covering body over the full section.
    2. Move the pocket to a free face.
    3. Remove the pocket if it lost its function.
- **Every screw can be put in and turned** (`access.screw_path`, block at design). In front of every screw head a straight path is free for 60.0 mm, for the screw and the key behind it. The part itself must not stand in the section of the head, nor farther out than 0.2 mm anywhere in the path as wide as the head plus 1.0 mm: not even a thin ledge over the edge of a head seat. The part itself in the ring of the tool clearance right at the top of the head is the wall of the recess the head sits in, not a ledge. Other solid bodies in the paths of a part are ONE row and one question for the part, whatever they are: every covering part nearest first, with its distance and the heads it covers; free only if each is fitted later and comes off first, with its removal order named. A body inside a bought or linked assembly is named with the assembly it belongs to. A mesh in the path leaves the check open. Every fastener exists as a ref body before this check runs; a screw that is only a hole is tested as a hole. A hit under 2 % of the section is noise; a body the owner named an overlay (access.overlay_pattern) is not counted.
  - Fix, in this order:
    1. Cut the ledge back to the head seat plus the tool clearance.
    2. Move the screw, or let it come in from the free side.
    3. A head under a part fitted later: answer the assembly-order question with the removal order.
    4. A screw that is serviced on the finished machine: add a service hole through the cover.

Asked by the gate. Each is answered with evidence, never with a typed number.

- **A bridge layer is meant, and opened after the print** (`access.bridge_layer`, attest at design, once per finding). A one-layer skin between a counterbore that faces the bed and its hole is a print aid: the ring round the hole cannot print in the air. It is allowed when it is meant, and the build notes say that the hole is pushed or drilled through after the print. A skin of the same thickness that a join or a fill left behind is a closed hole.
  - Question: "Hole {feature} ends in a skin one bridge step thick, with hole {by} behind it. Is it a bridge layer that is opened after the print?"
  - Fix, in this order:
    1. A leftover skin: cut the hole through all, after the last join or fill of the zone.
    2. A bridge layer: write into the build notes which holes are opened after the print, and with what.
- **No hole is closed by supports** (`access.hole_supports`, attest at export, once per changed part). No hole, tunnel or slot is closed by supports. A hole whose axis is off the print axis gets a roofed section, or a support blocker in every project it is placed in, or it is sliced and looked at. A mesh swap keeps a blocker where it was: after a hole moves, its blocker is checked again.
  - Question: "For every hole, tunnel and slot of this part that is off the print axis: what keeps supports out of it?"
  - Fix, in this order:
    1. Turn the part so the hole axis is the print axis.
    2. Give the hole a roof that prints without support.
    3. Put a support blocker over the hole in every project that holds the part, and check it again after the hole moves.
- **The key can turn at every screw** (`access.key_swing`, attest at design, once per changed part). A screw is reachable only if the key can also turn. Over each head 27.0 mm is free for a key with a 20.0 mm short arm, or a straight line for a long key exists. The long arm (60.0 mm) sweeps at least 180.0° free of every part fitted before it, with 5.0 mm beside the arm.
  - Question: "For every screw of this part: can the key turn, or is there a straight line for a long key?"
  - Fix, in this order:
    1. Move the gusset, rib or wall out of the swing of the key.
    2. Move the screw to where the key can turn.
    3. Open a straight line for a long key through the part that stands in the way.
- **Every hole has a known purpose** (`census.unknown_hole`, attest at design, once per finding). A hole the census cannot classify against the tables is named once: a blind one, or one with a bought body on its axis, through the question what goes in it (fits.pocket_content), every other one here. A short cylinder that is a piece of the same surface as another hole is not asked about. Named here too is a straight hole that is both a clearance hole and the old straight pocket of an insert, a blind hole of clearance size, and a pocket-sized cylinder whose wall is broken open, unless a screw ref lies on its axis. The answer is kept for that hole until the hole changes.
  - Question: "Hole {feature}: what is this hole for?"

Advice. No check exists yet: apply it by hand.

- **A head under a later part is a named exception** (`access.head_under_later_part`, advice). A screw head covered by a part fitted later is an exception with a name and a removal order: which part comes off first, and with which key. It is answered through the assembly-order question of the finding, never by leaving the screw ref out of the model. Where a standard length gives a flush head, prefer it.
- **Every fastener is a ref body before access is checked** (`access.screws_modelled_first`, advice). Every fastener exists as a ref body before the access check runs. A screw that is only a hole is tested with the section of the hole, not with the head and the key: the head path is not proven.
- **What is adjusted later has a tool path on the finished machine** (`access.service`, advice). Anything adjusted or replaced after assembly (belt tension, preload, set screws, a switch) has a tool path on the finished machine. The key type and its reach are stated. A covered screw gets a service hole or a recorded removal order.

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category access`, or `gate/RULES.md` in a gated project.

<!-- gate:rules access end -->

### Clearance to neighbours

<!-- gate:rules clearance start -->
<!-- Generated from rules/registry/clearance.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **Gap to every moving neighbour** (`clearance.min_gap_moving`, block at design). A part keeps at least 2.0 mm to every neighbour of another declared motion group (printed, bought, hardware and mesh-only bodies within 5.0 mm), measured as the least distance in the pose as modelled. Clearance is a measured minimum gap, never "0 mm3 of overlap"; a coplanar pass counts as a collision. Parts that are fastened to each other move together and belong to one motion group: a gap of 0 between two groups is a collision or a wrong declaration. A mesh-only body is known by its bounding box: its box at the full gap or farther is a pass, its box nearer than that decides nothing and is reported as not checked, never as a failure. A part in no motion group is not measured, and the report says so for the part; the scan says it once for the document. The motion groups are declared in the layout of the project. The travel between the modelled poses is not covered here (clearance.sweep_full_travel).
  - Fix, in this order:
    1. Move the part or its neighbour apart.
    2. Shape the moving feature so that it passes; do not carve the stationary part for it.
    3. Cut this part back along the real envelope of the moving neighbour, to the full gap, and leave no thin ledge.
    4. A gap that must be smaller: owner waiver that names the pair and the measured gap, with a hand test before power.

Asked by the gate. Each is answered with evidence, never with a typed number.

- **No overlap with any neighbour in the static pose** (`clearance.static_all_neighbours`, attest at design, once per changed part, deferred: registered, not asked or run yet). Every new or changed part is intersected in the static pose with every neighbour, stationary against stationary too: hardware refs, heads of fasteners and ties, cables, springs and parts that exist only as meshes. Allowed overlaps are a named list with their expected volumes (an insert in its pocket, a press fit); anything else fails.
- **Every joint is swept over its full travel** (`clearance.sweep_full_travel`, attest at design, once per changed part, deferred: registered, not asked or run yet). After every edit each affected joint is swept over its full travel and every adjustment position, both ends of a slide included. The list of fixed bodies is generated: every body that does not turn, the joint base included, never a hand-picked list. The step is 1.0°, and 0.5° within 10.0° of each stop: a coarse step jumps over a rib. The hard stop is the first contact. A mechanism is checked at several phases. The gap in every pose is at least 2.0 mm.

Advice. No check exists yet: apply it by hand.

- **Unconfirmed clearances get a hand test** (`clearance.hand_test_before_power`, advice). Every clearance under the gap rule that no print has confirmed is listed with a hand test: the joint is turned through its whole travel by hand before power is applied.
- **A mirrored feature is checked on its new side** (`clearance.mirror_other_side`, advice). A mirrored or moved feature is checked again against what exists only on the new side: screw heads, slots, cables. Where a part lies over a neighbour's screw heads it gets reliefs with 1.0 mm round each head.
- **Shape the moving feature, not the stationary part** (`clearance.no_cut_stationary`, advice). Do not carve a stationary part to let a moving feature pass: shape the moving feature. Nothing printed stands over a moving belt, and a belt gets 0.5 mm of side room for wander on top of the gap.
- **A face printed over supports needs a wider running gap** (`clearance.supported_face`, advice). A running clearance to a face that was printed over supports is 1.2 mm, not the gap that works against a clean face (about 0.2): a face on supports comes out rough.

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category clearance`, or `gate/RULES.md` in a gated project.

<!-- gate:rules clearance end -->

### Fits

<!-- gate:rules fits start -->
<!-- Generated from rules/registry/fits.json by tools/gate/gate_render.py. Do not edit here: change the registry, then render again. -->

Checked by the gate. A finding stops the stamp of the part.

- **A pocket for a bought part takes its size from a printed fit** (`fits.bought_part_pocket`, block at design). A pocket, seat or bore that receives a bought part (a magnet, a pin, a nut, a board) takes its size from a named fit parameter, never from a number typed in a script. While that parameter is D (decided, never printed) for the nozzle, material, hole compensation and layer height of the project, the part is not exported: a coupon print makes it M, or the owner makes it U. A pocket whose size matches no declared fit parameter is asked once: what goes in.
  - Fix, in this order:
    1. Print a coupon first: a small block with the pocket in three sizes, in the real material, from the real .3mf project. Try the bought part in each.
    2. Record the size that fits as the parameter: python3 gate/tools/gate.py owner param NAME VALUE --tag M --source "coupon, date". It is stored with what it was printed under.
    3. Or the owner sets the size without a print: the same command with --tag U.
    4. Cut the pocket from the parameter, not from a typed number.

Asked by the gate. Each is answered with evidence, never with a typed number.

- **Fits that were never printed in this material are named** (`fits.decided_not_printed`, attest at export, once per changed part). Every fit size of this part that was never printed in the target material and on the target nozzle is listed for the owner. A size measured in another material or on another nozzle counts as decided (D) here.
  - Question: "Which fit sizes of this part were decided and never printed in the target material and on the target nozzle?"
- **What goes in this pocket** (`fits.pocket_content`, attest at design, once per finding). A blind hole that fits no row of the tables and no declared pocket parameter is named once: what goes in. A bought part goes in only with a fit parameter that is measured (M) or owner-set (U) for this project, and the pocket has that parameter's size.
  - Question: "What goes in the pocket {feature}? If a bought part goes in, name the fit parameter its size comes from."

Advice. No check exists yet: apply it by hand.

- **A bought part is measured before a part depends on it** (`fits.bought_part_measured`, advice). A bought part is modelled from its real model file and measured before any part that depends on it is printed. Vendor models can be off by a millimetre. Each batch is measured at its functional features. A photo is not a measurement. A measurement updates every copy of the reference in every document. A position the owner did not give is asked for, with its datum.
- **Screws in slots do not locate** (`fits.keys_spigots`, advice). A part that must be centred or indexed gets a spigot, a dowel, a printed datum lip or a key with 0.1 mm per side. A bolt-on mount whose position matters is located by printed faces.
- **Each fit has one home: the model or the slicer** (`fits.model_or_slicer`, advice). Each fit has one home: the drawn size in the model, or the compensation in the slicer (0.125 mm per side here). Model clearance is not stacked on slicer compensation without saying so. A fit the slicer setting cannot reach is corrected in the model and recorded per material.
- **A fit is measured per material and nozzle** (`fits.per_material`, advice). A fit allowance is a parameter per material and nozzle: this part is judged for "PLA" on a 0.4 mm nozzle. A value measured in one filament is not carried to another. Only a real print earns the tag M, and its source names the printer, the nozzle and the filament.
- **Pin and dowel holes come from one table per material** (`fits.pins_dowels`, advice). Pin and dowel holes come from one table per material: press at the pin size, slip at the pin size plus 0.05 mm (PA6-CF 0.1), running at the pin size plus 0.3 mm; a printed peg is 0.2 mm under its hole. Small holes are checked go / no-go on a coupon.
- **Printed profiles that mesh keep a real clearance** (`fits.printed_mesh_clearance`, advice). Two printed profiles that mesh or roll on each other keep at least 0.12 mm per side. A clearance at a bed face allows for the first layer, which spreads by about 0.18 mm.
- **Scaling keeps the interfaces** (`fits.scaling_keeps_interfaces`, advice). Scaling a model scales the body only. Screw bosses, bearing seats and magnet pockets stay at their real size.
- **A sliding pair has clearance and a long guide** (`fits.sliding_pair`, advice). A printed part that slides in a printed part has at least 0.25 mm (PA6-CF 0.4) per side in the print plane (more in a filled nylon: see the per-material values). The guide is captured in every direction but its travel and is at least twice as long as the lever arm of the load.
- **A nut slot fits the nut the way it travels** (`fits.slots_nuts`, advice). A nut slot is sized for the nut in the orientation it travels: across flats in a pocket that holds it, across corners in a slot it turns in. A bolt through a bearing fills the bore.
- **A stack against a bought unit comes from the measured unit** (`fits.stack_from_measured`, advice). A stack height against a bought or assembled unit comes from the measured unit. An over-constrained part gets a relief or an allowance. A component that stands between a floor and a moving part is checked at both ends. No joint is designed to need shims to close.
- **A new fit is printed small and cheap first** (`fits.test_cheap_first`, advice). A new fit or joint is printed first as a cut-down slice of the real mesh, or as a coupon with 3 marked clearance steps. The entry lists what will be measured. The result goes back into the parameters. A sibling joint waits for the first test. The correction goes into the cheaper part or the slicer, never into a datum feature. A cheap test proves a fit in its own material only, and never strength.
- **A hole size holds for one wall order** (`fits.wall_order_for_holes`, advice). The wall order of the slicer changes small holes. With inner walls first, small fit holes scattered; with the outer wall first the scatter went away in one calibration. A hole size measured under one wall order holds only for that order: keep the order of the project, or measure the hole again after changing it. Hole compensation is per side (0.125 mm).

The incident behind each rule, the evidence a question takes and the numbers of a project: `gate.py rules --md --category fits`, or `gate/RULES.md` in a gated project.

<!-- gate:rules fits end -->

## Report what was actually checked

A result counts only if it comes from the current model. After any change, earlier checks prove nothing until they are run again. Report each check as passed, failed, or **not verified**, and never fold "not verified" into "passed".

## Check before calling a part done

- [ ] In a project with a gate: `gate.py run --changed` exits 0 and the stamp of the part exists (the `gate` skill). The lines below are for a project without one, and for what the gate does not check yet
- [ ] One flat bed face, and orientation noted
- [ ] Every hole and pocket clear after the last join
- [ ] `screw_access.py` finds no printed part in any screw's path (or each hit is a later-mounted part, named)
- [ ] Shafts and stems lie along their layers; clamps on a rotating axis have more than one screw or a shape lock
- [ ] `thin` ends `TOTAL 0` and `wall_thickness.py` shows no neck
- [ ] `insert_pockets.wall()` on every part with insert pockets: no FAIL or GROW row left (a wall under the target only where a named neighbour stops it and the owner waived that row)
- [ ] Mating parts checked against each other, stationary ones too, through the full travel of anything that moves
- [ ] Close-up screenshots looked at
- [ ] Every asymmetry and every "had to" change named to the user
