# Agent guide

Map for agents installing or working on fusion-bambulab.

## Installing in Claude Code

1. Check `claude --version` works.
2. Run the commands in [INSTALL.md](INSTALL.md), then `claude plugin list`, and confirm `fusion-bambulab` is enabled.
3. Tell the user to run `/reload-plugins` or restart, and to enable **Preferences > General > API > Fusion MCP Server** in Fusion.
4. Suggest `/fusion-bambulab:setup-project <name>` in a project folder. Do not create a project unasked.

## Installing in another agent

The skills are plain `SKILL.md` folders, but only Claude Code installs the plugin as a whole, and only Claude Code has been tested. Tell the user that.

1. Clone the repository somewhere permanent.
2. Copy each folder in `skills/` to wherever this agent loads skills from. Use its own documentation; do not guess the path.
3. In the copies, replace `${CLAUDE_PLUGIN_ROOT}` with the absolute path of the clone. Other agents do not expand it.
4. Add Fusion as an HTTP MCP server at `http://127.0.0.1:27182/mcp`, using this agent's own MCP configuration and keeping existing servers. If it cannot, use `tools/fusion/fusion_mcp.py`.
5. Confirm the agent lists the skills, then run `python3 <clone>/tools/fusion/fusion_mcp.py ping` with Fusion open.

## Repository map

| Area | Location |
|---|---|
| Plugin and marketplace manifests | `.claude-plugin/` |
| Fusion MCP connection | `.mcp.json` |
| Skills, the source of truth for behaviour | `skills/<name>/SKILL.md`, with `references/` |
| Bambu tools, vendored to `bambulab/tools/` in a project | `tools/bambu/` |
| Fusion tools, vendored to `fusion/tools/` in a project | `tools/fusion/` |
| Rule registry and size tables, vendored to `gate/rules/` in a project | `rules/registry/<category>.json`, `rules/tables/` |
| Print gate: the one command, judges, probes, stamps, the guard; vendored to `gate/tools/` | `tools/gate/` |
| Print ledger, vendored to `bambulab/tools/` beside `bambu3mf.py` | `tools/ledger/`, `tools/bambu/meshid.py` |
| Hooks: a session-start digest of the gate, silent in a project without one | `hooks/hooks.json`, `tools/gate/gate_hook.py` |
| Scaffolding | `tools/setup_project.py`, `templates/` |
| Tests and CI | `tests/`, `.github/workflows/ci.yml` |

## Changing things

- Run `python3 -m unittest discover tests` and `claude plugin validate . --strict`.
- The tests need neither Fusion nor Bambu Studio. A change to `.3mf` output also needs a real slice with `slice_check.py`, and for settings changes, opening in the GUI. Say so if you could not do that.
- Fusion scripts run against the user's real document. Keep test scripts read-only and limited to named bodies.
- Never write a `.3mf` that Bambu Studio has open, and never close a user's Bambu Studio just to run a test.
- Scope ends at a ready Bambu Studio project. Slicing for print and printer control belong to bambu-printer-mcp: do not add them here. The print ledger widens the scope by one step only: it reads what was written into a `.3mf` and what was printed (the owner's word, and the slicer's local job cache when the owner turns that on). It never starts, stops or changes a print and has no network code.
- A new design rule goes into the registry, `rules/registry/<category>.json`, not into a skill as prose. A rule of level `block` needs a judge in `tools/gate/gate_judges.py` and a fixture in `tests/fixtures/facts/`. Then run `python3 tools/gate/gate_render.py`: the rule blocks in the skills are generated, and `tests/test_render.py` fails while a block differs from the registry.
- The hook must stay harmless: exit 0 with no output wherever there is no `gate/project.json`, never exit 2, no Fusion call, well under a second. It runs the plugin's code only, never code from the project folder.
- Keep project details, personal paths and names out. Rules are general; numbers from one printer are labelled as such.
- Bump `version` in `plugin.json` for each release, or users get no update. Do not rename `name`.
