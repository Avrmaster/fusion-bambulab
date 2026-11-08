# Fusion API pitfalls

Each of these cost real work. None raises a useful error at the moment it goes wrong.

## Occurrences and transforms

- `occurrence.transform2 =` fails on a native occurrence inside a non-root component. Set it on the root-context proxy.
- `addNewComponentCopy(src, M)` and `addExistingComponent(comp, M)` can ignore `M`. Add with identity, set `transform2` in a **separate** script call, then verify. Set it on one occurrence of a component per call.
- After moving occurrences in a parametric design, call `design.snapshots.add()`, or later features revert the move.
- `transform2` raises "invalid argument value" if the axes are rounded and not exactly orthonormal. Compute them with `math.sin` and `math.cos` in the script.
- Nested occurrence bounding boxes read stale inside the same script. Verify in a separate call, and compare **body** world bounding boxes, not occurrence ones.
- `deleteMe()` on a native occurrence of a non-active component can leave it at root. Delete through the root-context proxy.
- A model can hold several occurrences of a component. Pick by `fullPathName`, never by `[o for o in all if o.component.name == NAME][0]`.
- `activeProduct` is not the Design in other workspaces. Use `activeDocument.products.itemByProductType('DesignProductType')`.

## Deleting and moving components

- **Never delete a sub-assembly emptied with `moveToComponent`.** Components created inside it die with it, history included, even after the move. Rename the shell, hide it, and leave it.
- If a shell must go: export bodies as backup, make Paste New copies (`addNewComponentCopy` keeps history), delete, then re-place every `addExistingComponent` occurrence, because the cascade drops captured positions. Diff volumes and feature counts against the record.
- Try `features.removeFeatures.add(occ)` first: Remove drops the occurrence without deleting components, unless it was the component's only occurrence.

## Sketches and features

- In a nested component, `sk.modelToSketchSpace(p)` takes **component-local** points. Convert world to local with the inverse of the root-context transform.
- Get an extrude direction from `sk.sketchToModelSpace` of (0,0,0) and (0,0,1), not from `xDirection × yDirection`.
- Sketches on a B-rep face project its edges, so new circles split into annuli. Sketch on a construction plane with `setByOffset(face, 0)` instead.
- A JOIN that only face-touches, or whose profile does not touch the target, makes a separate body. Order joins so each touches what is joined, or end with a combine and check `bRepBodies.count == 1`.
- Rounded sketch coordinates leave zero-area seams and crescents where curves should be tangent or coincident. Compute points from exact angles and radii in the script.
- Fill bodies must not share a surface with the part: make them 0.05 mm wider than the hole and run 0.3 mm into the solid.
- Never run `deleteFaceFeatures` on zero-area seam faces: it can "heal" by growing neighbours and removing real material. Zero-area seams are harmless.
- Closed fitted splines with hundreds of points run a dense solve and hang Fusion, and every moved fit point repeats it. Use one open spline per lobe or segment.
- Fitted splines accept existing SketchPoints as fit points, which closes a profile exactly.
- Emboss takes one single-loop profile per feature.

## Patterns

- `constructionAxes.createInput().setByLine(InfiniteLine3D)` fails in a parametric design. Use a construction SketchLine as the axis.
- A circular pattern of a loft cut with `IdenticalPatternCompute` can **join** instead of cut. Use `AdjustPatternCompute`, and check the volume dropped.
- Suppressing single pattern elements fails. Suppress the whole pattern and cut the wanted instances one by one.

## Editing history

- Editing an extrude's extent after suppressing other features can drop a later cut's target ("No target body"). Suppress the broken feature and append a new cut.
- As-built joints and rigid groups cannot be edited without rolling back ("Cannot be edited before rolling back"). Delete at value 0 and recreate, or add a new rigid group.
- Deleting bodies inside a base feature's `startEdit`, using objects fetched before it, raised "refers to a deleted Object" and dropped the design to Direct mode. Change bodies with a new base feature plus Remove and Move features.
- `finishEdit` on a base feature, and any timeline recompute, can reset body appearances.
- A body object held from before `finishEdit` fails as a combine tool with `ALL_TOOL_BODY_REFERENCE_LOST`. Take it from `baseFeature.bodies` after `finishEdit`.

## Joints

- For posing, occurrenceOne must be the **moving** part, or the joint drags the wrong way.
- The sign comes from joint geometry. Check with a small drive against recorded bounding boxes.
- Keep every pose joint at 0 before saving, exporting or checking. Bed orientations assume the model pose.

## Appearances

- Never edit `body.appearance` in place: the default appearance is shared, and recolouring it recolours the model. Use `design.appearances.addByCopy(libAppearance, 'unique name')`, recolour the copy, assign it.
- Appearances on base-feature bodies do not survive recomputes. Set them on the **occurrence**.
- A document can hold several appearances with one name, so `itemByName` returns one of them.
- Setting hundreds of appearances in one call times out. Do a few components per call.
- A physical material can come with an appearance that renders see-through (a PETG material showed parts as clear plastic, text and edges unreadable). Assign an explicit opaque appearance per occurrence.

## Add-ins and UI

- Event handlers created from an MCP script die when the script ends. An add-in with UI must be started from Utilities > Add-Ins > Run.
- `commandDefinition.execute()` from an MCP script never starts the command.
- Bodies visible in the API but not drawn: toggle their light bulbs off and on, then `activeViewport.refresh()`. After a base-feature edit, call `design.activateRootComponent()` if the model renders ghosted.
- To make a reference unclickable, set `isSelectable = False` on the root occurrence too.

## Meshes

- `MeshCalculator` across many bodies can facet one body for 20+ minutes and block every call. One part at a time, a few per call. Diagnose with `sample <Fusion pid> 2` on macOS.
- `displayMeshes.bestMesh` is instant but depends on what was last drawn, can be unstitched, and raises for a body never shown. An invisibly opened document has no display meshes. Fine for previews, not for printing.
- `exportManager` STL export can silently write nothing. Check the file exists and is closed.

## Recovery

- **Timeouts on tiny scripts:** Fusion may be busy with an autosave. Run a one-line read-only probe, then retry.
- **A full rebuild and an export in one call time out** even when each fits on its own. Run them as separate calls.
- **Fusion crashes on opening one document, after a hung or killed quit:** with Fusion closed, on macOS move (do not delete) `$(getconf DARWIN_USER_CACHE_DIR)/com.autodesk.fusion360`, then reopen. Next: reboot, then change the graphics driver in Preferences.
- **Hang on quit:** a save finishes in two steps, and the background publish runs for up to a few minutes. Wait for the version label to come back before quitting.
- **Read-only after a kill:** the user must click Make Editable; the API cannot.
- **Dropped to Direct mode** (`designType == 0`, `timeline` raises): close without saving and reopen the last saved version. If work must be kept, Save As a backup first, reopen the parametric version, and copy bodies across with `TemporaryBRepManager` into a base feature.
- **Save fails with InternalValidationError:** another save is running. Wait 20 s and retry.
- Undo is no safety net with concurrent sessions.
