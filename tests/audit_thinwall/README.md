# Independent audit of the thin-wall gate

Shares no code with the gate: the gate casts rays through the faces' display meshes; the audit asks the B-rep. For
every pair of faces of each printed body whose boxes come within 1.6 mm, Fusion's `measureMinimumDistance` gives the
closest points; the pair is a wall when plastic lies between them (`pointContainment`), their outward normals point
away from each other (not a slit) and look at each other within the corner angle. Faces that share an edge are read
at the edge (a convex edge sharper than the wedge angle is a knife edge); faces that touch without an edge, facing
away, are a wall of zero width. The audit then applies the rule numbers of `rules/thinwall.json` with its own code
(tooth rings, sacrificial layers, limits from its own reading of the .3mf) and compares, part by part, with the
gate's result files.

    AUDIT_FACES=faces.json python3 tests/audit_thinwall/audit_run.py <project> [PART ...] --out rows.json   # Fusion, resumable
    python3 tests/audit_thinwall/audit_judge.py <project> rows.json faces.json [PART ...]                 # agree / differ

`faces.json` comes from `audit_faces.py` (one call: every face's type, area, middle and axis). The Fusion side takes
about 75 min on Robot Arm V2 (the three toothed pulleys are most of it); it depends only on the model, so a change of
the gate needs only the judge again.

Fusion traps the audit had to learn (each one first showed up as a false difference):
- a face's evaluator works in its component's frame, proxy or not: points of the assembly go through the inverse of
  the occurrence's transform first (the J2 and J3 parts sit in moved occurrences);
- the evaluator's normal is the surface's, not always the face's: the outward sign is settled per face with a point
  0.01 mm off it;
- on a plane `measureMinimumDistance` can answer with a point outside the face (its unbounded plane): the two faces are
  then measured again as sheet bodies (`TemporaryBRepManager.copy`);
- `measureMinimumDistance` refuses the native faces of an occurrence's component: it is called on the proxies;
- two Fusion calls at the same time (another session, the gate) can fail with `count > 0`: `audit_run.py` retries.

The audit's own corner rule: closest points that lie on two edges across a corner (the line between them more than
the corner angle off both outward normals, neither face a sliver) are not a wall: the gate's corner rule, read on the
B-rep.

Robot Arm V2 v201, 2026-11-24 (gate 0.7.0): 34 of 39 parts agree. The 26 differences left, each checked:
- the gate thinner than the audit (j2/j3 switch cradles 0.69/1.19 vs 1.40, drive plate 0.58 vs 0.67 and 1.34,
  link 2A 0.74 vs 0.80): the gate's chord, probed with `pointContainment` every 20 %, is plastic over its whole length
  with air just past both ends. The plastic is that thin along that line; the audit's closest-point pair of the same
  faces lies elsewhere. The gate's number stands.
- the gate 0.03 thicker on a neck between two curved corners (drive plate 0.482 vs 0.453, both under one line): the
  neck fan runs on the mesh chords of the two holes; the B-rep minimum is the truth there.
- microscopic faces (0.001 to 0.15 mm2): the D3.1 channels of the drive plate graze its face y 43.2 by 0.05 mm and
  touch it at single vertices (the audit reads zero, the gate finds the same place a few mm off: 0.001 at the cone of
  the same channel); two 0.005 readings between faces of 0.01 mm2 that `pointContainment` cannot resolve; a 0.001 mm2
  face 0.3 from a plane on the J3 elbow tongue. Model crumbs, under one line both ways; found by one side, not placed
  the same by the other.
- the J3 elbow tongue's D6.54 bosses over D6.50 holes on the same axes (0.02 mm, axes 0.03 apart): the gate reports
  the family (faces_cross and tubes); where boss and hole only meet end to end (a 0.02 ledge, faces 72-78) the gate's
  coaxial rule calls it a step and the audit reads 0.015 across the ledge; four of the gate's crossings are not
  within reach of an audit pair.
