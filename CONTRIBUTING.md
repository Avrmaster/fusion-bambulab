# Contributing

The most useful contributions are lessons paid for with a failed print or a frozen Fusion, and reports from printers and platforms we have not tested.

## Scope

This plugin covers Fusion design, checked STL export and building Bambu Studio projects. Printer control and slicing for print are out of scope: [bambu-printer-mcp](https://github.com/DMontgomery40/bambu-printer-mcp) does those.

## Adding a lesson

Put it in the matching skill as a general rule:

- **What goes wrong**, including the symptom a person would search for
- **What to do instead**

Leave out project details. If a number comes from one printer or material, say which.

| Lesson about | Goes in |
|---|---|
| Fusion API behaviour | `skills/fusion-modeling/references/api-pitfalls.md` |
| The `.3mf` format | `skills/bambu-project/references/3mf-format.md` |
| Slicer and plate workflow | `skills/bambu-project/SKILL.md` |
| Part design | `skills/print-design/SKILL.md` |

## Changing tools

Keep the core tools on the Python standard library. Optional dependencies must fail softly or be named in the tool's docstring.

```bash
python3 -m unittest discover tests
claude plugin validate . --strict
```

Add a test when a bug could come back. Say in the PR what you tested on: OS, Fusion and Bambu Studio versions, printer and nozzle, and what you could not test.

## AI-assisted contributions

Welcome. Disclose them in the PR, and read the whole diff first: the person submitting is responsible for it.

## Releases

1. Bump `version` in `.claude-plugin/plugin.json`.
2. Run `claude plugin tag --push`.
3. Publish a GitHub release with the changes.
