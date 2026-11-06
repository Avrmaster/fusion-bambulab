---
name: setup-project
description: Set a folder up for designing in Autodesk Fusion and printing with Bambu Studio from Claude Code. Use when starting a new 3D-print or CAD project, or when a project needs a Bambu .3mf build script. Also refreshes a project's vendored tools.
argument-hint: "[project name]"
---

# Set up a Fusion + Bambu project

The tools live in `${CLAUDE_PLUGIN_ROOT}/tools`. Never copy them from a sibling project.

## 1. Printer template

A template holds the printer, nozzle, process and filament presets, without any parts. It is made once per printer and reused by every project.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/bambu/bambu_template.py list
```

- **One template:** use it, and name it in your report.
- **Several:** run `show` on each and pick by the printer or nozzle the user named. Ask only if nothing settles it.
- **None:** ask the user for any `.3mf` they saved from Bambu Studio on this printer, with the filaments they use loaded. Any project works; its parts are dropped. If they have none: open Bambu Studio, choose printer, nozzle, plate and filaments, add any model, File > Save Project As. Then:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/bambu/bambu_template.py make <saved.3mf> <short-name>
```

`make` prints the process overrides the source project carried. Every new project inherits them, so tell the user what they are.

If the project is Fusion-only, or the user has no Bambu printer yet, skip this step: everything else still works.

## 2. Scaffold

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/setup_project.py <dir> --name "<Name>" --template <short-name> --fusion-doc "<Fusion document>"
```

It never overwrites existing files, so it is safe in a folder that already has work in it. It creates:

| Path | Purpose |
|---|---|
| `CLAUDE.md` | a section that points future sessions at this workflow |
| `DESIGN_RULES.md` | fits and decisions, tagged measured or not |
| `fusion/parts.json` | the printed parts and where they are in Fusion |
| `fusion/tools/`, `bambulab/tools/` | vendored tools, so the project builds without the plugin |
| `bambulab/template.3mf`, `bambulab/build_3mf.py` | the printer settings and the project's build script |
| `stl/`, `.shots/` | exports and screenshots |

## 3. Fusion connection

```bash
python3 fusion/tools/fusion_mcp.py ping
```

The plugin brings the Fusion MCP server with it, so a project needs no `.mcp.json`. If it does not answer, ask the user to start Fusion and enable **Preferences > General > API > Fusion MCP Server**. A port other than 27182 needs `FUSION_MCP_URL` for `fusion_mcp.py`, and a project `.mcp.json` with that URL.

The server connects only at session start. If Fusion started later, run `/mcp` to reconnect, open a new session, or use `fusion_mcp.py run` meanwhile.

## 4. Report

State the folder, the template and its printer, whether Fusion answered, and what to do next. Continue with `fusion-modeling`, or with `bambu-project` if the STLs already exist.

## Refreshing tools

After a plugin update, in an existing project:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/tools/setup_project.py <dir> --update-tools
```

This overwrites the vendored copies. Check `git diff` first if the project has local fixes.
