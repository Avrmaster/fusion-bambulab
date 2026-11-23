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
