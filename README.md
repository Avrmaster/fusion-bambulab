<p align="center">
  <img src=".claude-plugin/icon.png" alt="fusion-bambulab" width="120" />
</p>
<h1 align="center">fusion-bambulab</h1>
<p align="center">
  <strong>Claude Code designs in Autodesk Fusion and builds your Bambu Studio project.</strong>
</p>
<p align="center">
  <a href="https://github.com/Avrmaster/fusion-bambulab/actions/workflows/ci.yml"><img src="https://github.com/Avrmaster/fusion-bambulab/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Avrmaster/fusion-bambulab?style=flat" alt="License"></a>
</p>

## Install

**Claude Code:**

```text
/plugin install fusion-bambulab --marketplace Avrmaster/fusion-bambulab
```

**Another agent:**

```text
Install the fusion-bambulab skills from https://github.com/Avrmaster/fusion-bambulab, refer to the repo's AGENTS.md for instructions.
```

Then turn on Fusion's MCP server: [two clicks, with screenshots](INSTALL.md#turn-on-fusions-mcp-server). Update, uninstall and older versions: [INSTALL.md](INSTALL.md). What each version changes, and what to check before refreshing a project: [CHANGELOG.md](CHANGELOG.md). Only Claude Code has been tested so far.

## What it does

Claude can drive Fusion and write `.3mf` files, but every new project starts from zero: it rediscovers the file format, hangs Fusion with a whole-model script, or produces a project Bambu Studio refuses to open. This plugin holds that knowledge once, so a new project starts from what the last one learned.

<table>
<tr>
<td width="50%">

### Before

> "Bambu says *Invalid configuration file*."
>
> "Fusion has been frozen for 20 minutes."
>
> "Why is there a solid slab over my screw holes?"
>
> "Copy the build script from my other project and change the printer again."

</td>
<td width="50%">

### After

> `/fusion-bambulab:setup-project Bracket`
>
> Then: *Design a wall bracket for a 25 mm pipe, export it, and make a Bambu project.*
>
> Parts are checked before export, every STL carries its Fusion version, and rebuilding keeps what you changed by hand in Bambu Studio.

</td>
</tr>
</table>

## Examples

Real projects, from idea to a plate ready to print. Both screenshots are straight from Fusion and Bambu Studio.

**A mechanical assembly:** bearings, steel pins, a motor and a full set of printed parts.

![Cycloidal gearbox: the idea, the assembly in Fusion, and all parts on a Bambu Studio plate](docs/example-gearbox.jpg)

**A simple object:** one prompt, then a few rounds of changes.

![Brush cube: the idea, the model in Fusion, and the sliced project in Bambu Studio](docs/example-cube.jpg)

## What's inside

The plugin connects Claude Code to Fusion's built-in MCP server, so every session has the Fusion tools once Fusion is running. No per-project setup.

| Skill | Does |
|---|---|
| `/fusion-bambulab:setup-project` | Sets a folder up: printer template, build script, design-rules file |
| `fusion-modeling` | Safe Fusion scripting, and the API traps that fail silently |
| `fusion-export` | Checked STL export, stamped with the Fusion version, plus stale-part and thin-feature checks |
| `bambu-project` | Builds and updates `.3mf` projects from code, keeping what you changed by hand |
| `print-design` | Orientation, tolerances, inserts, test prints, and what to do with a flawed print |
| `gate` | The print gate: registered rules, one check command, stamps tied to the geometry, a guard in the `.3mf` writer |
| `print-ledger` | What was written into each `.3mf` and what is already printed, before a printed part's interface moves; the owner's words are committed word for word, and the optional reader of the slicer's job cache is off until the owner switches it on |
| `fusion-cam` | CNC from Fusion Manufacture: stock that matches the part, helical holes, facing, GRBL-safe posting, `cam_refresh` and `nc_check` |
| `cnc-station` | A laptop with gSender next to the machine, fed from the Mac: job pages with steps, sync, read-only GRBL queries |

Claude picks all but the first up on its own when the task calls for them.

## Requirements

- [Claude Code](https://code.claude.com) 2.1.139 or newer, and Python 3.9+ as `python3` on your PATH
- Autodesk Fusion with **Preferences > General > API > Fusion MCP Server** on ([how, with screenshots](INSTALL.md#turn-on-fusions-mcp-server))
- Bambu Studio, for the print side

The core tools use only the Python standard library. `numpy`, `scipy` and `shapely` add wall-thickness checks, bed packing and exact plate checks.

Developed and tested on macOS with a Bambu Lab P2S. Windows paths exist but have not been tested on real Fusion or Bambu Studio. Bed size comes from your printer's template, so other Bambu printers should work: [tell us how it goes](https://github.com/Avrmaster/fusion-bambulab/issues).

## A project looks like this

```text
my-project/
├── CLAUDE.md            points every session at this workflow
├── DESIGN_RULES.md      fits and decisions, tagged measured or not
├── fusion/parts.json    which Fusion bodies are printed parts
├── stl/                 checked exports
├── gate/                optional: the print gate (rules, stamps, the owner's numbers)
└── bambulab/
    ├── build_3mf.py     plates, orientation and settings, as code
    ├── template.3mf     your printer's presets
    └── Project.3mf      open this in Bambu Studio
```

Tools are copied into each project, so it keeps building without the plugin and an old project does not change under you.

## The print gate

A design rule that lives only in prose gets forgotten, and a print pays for it: a thin wall round an insert, a bearing held by nothing, a hole closed by a later part. The gate keeps the rules in a registry (`rules/registry/`), each with the incident behind it, a number a project can set, and where possible a check. One command finds the parts, runs the checks and sums the verdict:

```bash
python3 <plugin>/tools/setup_project.py my-project --gate   # add the gate to a project
python3 gate/tools/gate.py run --changed                    # check what changed in Fusion
python3 gate/tools/gate.py export demo/bracket              # checked STL plus its stamp
python3 gate/tools/gate.py verify                           # every mesh of the .3mf has a valid stamp
```

A part counts as checked only when its stamp exists, and the stamp dies when the geometry changes. A project starts in `observe` mode, where the `.3mf` writer only reports unstamped meshes; `enforce` refuses them. The writer also compares the slicer settings every fit depends on (hole compensation, layer height and the like) with the file on disk at each save, and `verify` holds them against what the project declares: a change no tool was asked to make is refused in `enforce` mode and is one loud line in `observe` mode. Waivers and looser numbers are the owner's: the project's permission rules put a prompt in front of them. Without `gate/project.json` the guard, the stamps and the checks do not run. The `gate` skill has the workflow.

Since 0.2.0 the plugin ships one SessionStart hook (`hooks/hooks.json`): at the start of every session it runs `tools/gate/gate_hook.py` from the plugin, which looks for `gate/project.json` in the session folder and above it. Where there is none it prints nothing; in a gated project it prints the short gate digest into the session. It reads only local files, calls neither Fusion nor the network and never runs code from the project. It writes nothing, except in a gated project the git-ignored cache of STL hashes (`gate/run/stl_hashes.json`). It goes away only with the plugin (or with all hooks, via Claude Code's `disableAllHooks` setting).

A blocking verdict needs a measurement. What the gate could not measure (a mesh body in a path, a screw it did not recognise, facts of an older check) is listed as `NOT_RUN` with the one thing to do about it, never as a failure and never as a pass. Hardware is found by name and confirmed by shape, so a screw drawn as a head body and a shank body, or a bearing inside a bought assembly, is still a screw and a bearing; the name patterns are parameters of the project. Equal findings on a patterned feature are one line in the report. For a model that already exists, `gate.py run --audit` records what is there as inherited findings for the owner to decide, and asks nothing about parts that did not change. After the owner has adopted that list, a later audit only proposes: a new part is never inherited by itself. A question the facts of a part prove pointless is answered by the gate; a name alone never proves that, and an answer that the facts contradict is refused and asked again.

## Good to know

- In sessions where Fusion is not running, the connector shows as failed in `/mcp`. That is harmless.
- Scripts drive your real Fusion document. Save before heavy work.
- Files are only rewritten with Bambu Studio closed; a wrapper closes and reopens it for you.
- A `.3mf` that slices in the CLI can still be refused by the GUI, so open a new project in the GUI once.
- On PLA, layers that take very different times show as bands on the walls. `slice_check.py` points them out, and the `bambu-project` skill evens them out. PETG and nylon blends don't need this.
- The numbers in `print-design` come from one printer. Measure your own.

## Printing from Claude

This plugin stops at a ready Bambu Studio project: you open it, slice and print. To go further from Claude, add [bambu-printer-mcp](https://github.com/DMontgomery40/bambu-printer-mcp), which handles slicing, sending to the printer, AMS and monitoring. The two split the work:

```text
fusion-bambulab        Fusion design -> checked STL -> Bambu Studio project
bambu-printer-mcp   project -> sliced file -> printer
```

It is a separate project with its own licence (GPL-2.0), and it can start prints and heating without asking, so read its hardware safety notes first.

## How it compares

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/comparison-dark.svg">
  <img src="docs/comparison-light.svg" alt="Comparison of fusion-bambulab with six related projects across Fusion, Bambu project, printing and install features" width="100%">
</picture>

Each of these does something this plugin does not:

- **[bambu-printer-mcp](https://github.com/DMontgomery40/bambu-printer-mcp):** slicing, printing, AMS and monitoring over LAN
- **[bambu-studio-ai](https://github.com/heyixuan2/bambu-studio-ai):** idea to print in one flow, with model search and AI generation
- **[3dprint-scout](https://github.com/madrobotnet/3dprint-scout):** reference-first design with strict, evidence-based checks
- **[fusion360-bambu-print-skill](https://github.com/thibaud-sanchez/fusion360-bambu-print-skill):** print modes, and checking a saved project against expected parts
- **[bambu-3mf](https://github.com/woolimi/bambu-3mf):** projects built from system profiles, on many printer models
- **[bambu-studio-model-builder](https://github.com/DaveBitter/bambu-studio-model-builder):** quick single-object iteration with live reload in Claude Cowork

Several `.3mf` facts in `bambu-project` were confirmed by, or first learned from, these projects. See something wrong in the table? [Open an issue](https://github.com/Avrmaster/fusion-bambulab/issues) or edit `docs/comparison.py`.

## Contributing

New Fusion traps, `.3mf` rules and reports from other printers are the most useful contributions. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Autodesk or Bambu Lab. Fusion and Bambu Lab are trademarks of their owners.
