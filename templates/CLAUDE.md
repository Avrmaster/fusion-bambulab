<!-- fusion-bambulab:start -->
# {{NAME}}

3D-printed project: Fusion design -> STL -> Bambu Studio project. Set up by the fusion-bambulab plugin.

- Fusion document: {{FUSION_DOC}}
- Bambu project: `bambulab/{{PROJ}}`, built by `bambulab/build_3mf.py`
- Printed parts: `fusion/parts.json`; checked STLs go to `stl/`
- Design decisions and measured fits: `DESIGN_RULES.md`. Read it before changing a part, cite the rule, add what a print teaches.
- Screenshots: `.shots/` (Fusion cannot write to temp sandboxes)

Use the plugin's skills: `fusion-modeling` before any Fusion script, `fusion-export` to export, `bambu-project` before touching a .3mf, `print-design` for part decisions.

Never write a .3mf while Bambu Studio has it open. `bambulab/tools/` and `fusion/tools/` are vendored from the plugin: fix them there, or note the local change here.
<!-- fusion-bambulab:end -->
<!-- fusion-bambulab:gate:start -->
## Print gate

This project has a print gate in `gate/` (fusion-bambulab plugin). Use the `gate` skill. A part is checked only when its stamp exists: a report or a printed table is not a pass.

- Before changing a part: `python3 gate/tools/gate.py status`, and read the rules with this project's numbers in `gate/RULES.md`.
- After changing a part: `python3 gate/tools/gate.py run --changed` until it exits 0. Exit 3 means call it again; 1 means open findings or checks that could not decide (`NOT_RUN`: do what the row says, it is never a pass); 2 means questions to answer; 4 means the gate itself could not run.
- Export only with `python3 gate/tools/gate.py export <part>`. It writes the STL and its stamp together.
- Before and after a `.3mf` write: `python3 gate/tools/gate.py verify`.
- `gate/owner/` and `gate/project.json` belong to the owner. Only the owner waives a finding, loosens a number or switches the mode. Put a proposal in `gate/proposals/` and ask.
- `gate/tools/` and `gate/rules/` are vendored from the plugin: change them there, then `setup_project.py <dir> --update-tools`.
<!-- fusion-bambulab:gate:end -->
