# How to install

## Install

In Claude Code:

```text
/plugin install fusion-bambulab --marketplace Avrmaster/fusion-bambulab
```

Or from a terminal:

```bash
claude plugin install fusion-bambulab --marketplace Avrmaster/fusion-bambulab
```

On Claude Code older than 2.1.292, add the marketplace first:

```text
/plugin marketplace add Avrmaster/fusion-bambulab
/plugin install fusion-bambulab@fusion-bambulab
```

Then run `/reload-plugins`, or restart Claude Code.

## Verify

```bash
claude plugin list
```

`fusion-bambulab` should show as enabled, and `/fusion-bambulab:setup-project` should autocomplete.

## Turn on Fusion's MCP server

In Fusion, open **Preferences > General > API** and enable **Fusion MCP Server**. Note the port; the default is 27182.

The plugin connects to `http://127.0.0.1:27182/mcp`. It attaches at session start, so if Fusion started later, run `/mcp` to reconnect or open a new session. When Fusion is not running, the server shows as failed in `/mcp`, which is harmless.

If Fusion shows another port, add a `.mcp.json` with that URL to the project.

## Update

```bash
claude plugin marketplace update fusion-bambulab
claude plugin update fusion-bambulab@fusion-bambulab
```

Auto-update is off by default for third-party marketplaces. Turn it on under `/plugin` > Marketplaces.

Existing projects keep their copy of the tools until you refresh them. Ask Claude to refresh them with `setup-project`, and review `git diff` afterwards.

## Uninstall

```bash
claude plugin uninstall fusion-bambulab
claude plugin marketplace remove fusion-bambulab
```

Your projects keep working: their tools are copied in. Printer templates stay in `~/.config/fusion-bambulab/`.

## Try a local copy

```bash
claude --plugin-dir /path/to/fusion-bambulab
```
