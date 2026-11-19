# Contributing

The most useful contributions are lessons paid for with a failed print or a frozen Fusion, and reports from printers and platforms we have not tested.

## Scope

This plugin covers Fusion design, checked STL export and building Bambu Studio projects. Printer control and slicing for print are out of scope: [bambu-printer-mcp](https://github.com/DMontgomery40/bambu-printer-mcp) does those.

The print ledger is the one step past a ready project: it records what the tools wrote into each `.3mf` and what was printed, from the owner's word and, only when the owner turns it on, from the slicer's local job cache. It reads. It never starts, stops or changes a print, connects to no printer or cloud account, and stores no credentials.

## Adding a lesson

A lesson about part design is a rule, and rules live in the registry: `rules/registry/<category>.json`. Give it:

- an `id`, a `title` and a `statement`, with every number as a `{parameter}` that a project can set
- `why`: the symptom that paid for it
- `fix_order`: what to do first, second, last
- a `level`: `block` when a judge can check it, `attest` when only a question can, `advice` otherwise
- for an `attest` rule: the question, the evidence kinds and the fixed labels. Tie a label to facts where a probe has them (`held`: `{"<label>": {"none": ["fusion_screws.screws"]}}` refuses "none" while that list holds a row, `some` refuses a label that speaks of what the part does not have). `param` narrows which parameter counts as evidence. A question about the whole part that cannot apply to every part says so with `auto`: `[{"when": ["no_screw_feature"], "label": "this part has no screws"}]` lets the gate give that answer itself when the facts prove it (the fact tests are in `tools/gate/gate_attest.py`; each has a fixture pair in `tests/fixtures/questions/`: a case that must be asked and its twin that is answered). A test may say "proven" only when every unit it reads has facts and every list it reads is empty: "nothing recognised" is no proof, a unit that never ran gives "cannot say", and a name may only make a question asked. Each test has a twin in `FACT_SHOWN` that says what the facts show (a screw at the part, not a hole a screw could use): an answer that denies it is refused, and a stored one is asked again

A `block` rule needs its judge in `tools/gate/gate_judges.py` and a fixture in `tests/fixtures/facts/` built from the incident; the tests refuse a judge without one. A number that is not measured on a print keeps the tag `D`. Then run `python3 tools/gate/gate_render.py`: the rule blocks in the skills are generated from the registry, so never type a rule into a skill between the `gate:rules` markers.

Other lessons go into the matching skill as a general rule:

- **What goes wrong**, including the symptom a person would search for
- **What to do instead**

Leave out project details. If a number comes from one printer or material, say which.

| Lesson about | Goes in |
|---|---|
| Fusion API behaviour | `skills/fusion-modeling/references/api-pitfalls.md` |
| The `.3mf` format | `skills/bambu-project/references/3mf-format.md` |
| Slicer and plate workflow | `skills/bambu-project/SKILL.md` |
| Part design: a rule with a number, a check or a question | `rules/registry/<category>.json` |
| Part design: method and judgement that is not one rule | `skills/print-design/SKILL.md` |
| Working with the gate | `skills/gate/SKILL.md` |

## Changing tools

Keep the core tools on the Python standard library. Optional dependencies must fail softly or be named in the tool's docstring.

```bash
python3 -m unittest discover tests
python3 tools/gate/gate_render.py --check
claude plugin validate . --strict
```

The tools must run on Python 3.9, the interpreter macOS ships: run the tests with it as well.

Add a test when a bug could come back. Say in the PR what you tested on: OS, Fusion and Bambu Studio versions, printer and nozzle, and what you could not test.

## AI-assisted contributions

Welcome. Disclose them in the PR, and read the whole diff first: the person submitting is responsible for it.

## Releases

1. Bump `version` in `.claude-plugin/plugin.json`.
2. Add the version's section to `CHANGELOG.md`: what is new, what changes for a project after `--update-tools`, privacy and side effects, requirements, what was not tested.
3. Run `claude plugin tag --push`.

No GitHub release: users update from `main` by the version, the tag marks it, and `CHANGELOG.md` is the release note.
