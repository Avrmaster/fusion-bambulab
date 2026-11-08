---
name: fusion-modeling
description: Rules for driving Autodesk Fusion through its MCP server. Use before writing or running any Fusion script, creating or editing geometry, components, sketches or joints, or when Fusion hangs, crashes, times out or a script's result looks wrong.
---

# Scripting Fusion safely

Fusion is one process, often shared by several sessions. A stuck script blocks all of them and can lose unsaved work, and has frozen the whole machine.

## Script contract

- The plugin provides the Fusion MCP server. Its tools end in `fusion_mcp_execute`, `fusion_mcp_read` and `fusion_mcp_update`; the prefix depends on how it was configured.
- Call `fusion_mcp_execute` with `featureType: "script"`. The script must define `def run(_context: str):`. Without it the call fails and the edits are rolled back.
- **Define `run` in every script, even a helper-only one.** The server keeps one Python namespace between calls and calls whatever `run` it finds, so a script without its own runs the previous script's `run` again. If that old `run` exported or saved, every later call silently exports and saves again.
- Set `readOnly: true` for anything that only looks. It also runs while a command dialog is open.
- **A script that raises rolls back the whole call**, including steps that had succeeded. Run risky steps in separate calls.
- Units are **centimetres** in the API. Convert at the edges, and print in mm.
- Make every script idempotent: find things by name, and return cleanly if the work is done. A call can time out and still finish, so never re-run blindly: read the state first.
- Print a unique START and END marker. Output from other sessions can appear in your result.
- Non-ASCII text in a script arrives mojibaked, even as `\u` escapes, and name matches then fail silently. Keep names in files read with `encoding='utf-8'`, or fix with `s.encode('latin-1').decode('utf-8')`. Print a match count before acting on a loop.
- `fusion_mcp_read` with `queryType: "apiDocumentation"` gives the API reference. Check it instead of guessing a signature.
- No Fusion tools in this session? Use `python3 fusion/tools/fusion_mcp.py run <script.py> [--write]`.

## Light touch

- One component per call, each well under a minute.
- Never loop over the whole model: no meshing, interference or distance checks across every body. A bounding-box prefilter over the whole model is still a whole-model loop. Name the candidates: about 20 bodies per call.
- Before a sweep or clash check, compare bounding boxes by hand and drop parts that cannot reach each other.
- If Fusion stops answering, **stop calling it** and tell the user. Do not queue more calls.
- Ask the user to save before anything heavy or risky. Do not save their document unless they told you to.
- **Do not switch the active document** while the user works in Fusion. Work on another open document through `app.documents` (see [api-pitfalls](references/api-pitfalls.md)).
- **In a big assembly every feature recomputes the whole design**, which takes seconds per feature in a large one. For many cuts or fills, build the tool bodies with `TemporaryBRepManager`, add them in one base feature and apply one combine: one recompute instead of one per feature.

## Modelling

- Drive dimensions from **user parameters** with named, commented expressions, so a change is one edit and the timeline rebuilds.
- Build clean: all solids first, all cuts last. A JOIN after a CUT refills holes; re-check every hole and pocket after any join.
- Extrude an outline as one solid, then cut the openings. Picking which sketch regions to extrude, by size or bounding box, can keep a region meant to stay open, and a bore comes out closed.
- Set `participantBodies` on every cut and join. The default hits bodies of **other components** silently.
- Name components, bodies and features in plain words saying what or where the thing is, not axis codes like `+Y`.
- **Change the existing document in place.** Fusion versions and git keep the history. A test copy of a design splits every reference, export map and slicer project in two, and merging it back costs more than the change.
- **Reference documents directly.** An assembly inserts each design it uses as its own external reference. A wrapper document that only holds another design adds a layer that must be replaced and deleted later.
- Bought parts are references: keep them in their own components, never as bodies inside a printed part's component. Export takes the part's own bodies only.
- Never call `rollTo`, edit old features, or mass-delete features from a script. To fix something, append a corrected feature at the end. Check `design.designType` is parametric (1) at the start and end of an editing script: the design can silently drop to Direct mode and lose its timeline.

## After every edit

1. Read back what you changed: body count, volume, world bounding boxes. Sum volumes across bodies, since a cut can split one body in two. **Compare the volume with a rough hand estimate** (rings, discs, plates): a part 50 % heavier than the sum of its shapes has an opening filled in.
2. Scan the timeline for features that landed in a component you did not edit.
3. Take close-up screenshots with `fusion_mcp_read` `queryType: "screenshot"`, from above and below, and **look at them**. Numeric checks pass while slots, misaligned faces and clipped holes are plainly visible. To file one, use `app.activeViewport.saveAsImageFile` into `.shots/`. Show only that part and fit the view to all of it: a view cropped to half the part hides exactly the faults you are looking for. Keep `viewport.camera` before you start and assign it back at the end, so the user's view is where they left it. After a change, shoot only the parts that changed.
4. Run the `thin` check from the `fusion-export` skill: it must end with `TOTAL 0`, or every hit must be intentional.
5. Show the user any geometry you added only as a consequence of something else, with a simpler alternative.

## When it goes wrong

Read [references/api-pitfalls.md](references/api-pitfalls.md) before working with occurrences, nested components, transforms, patterns, joints, appearances or base features, and whenever a result surprises you. Its recovery section covers hangs, crashes on open and the drop to Direct mode.
