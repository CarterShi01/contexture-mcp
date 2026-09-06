# Verify with Claude Code

```bash
uv sync --extra dev
```

## Register

`claude mcp add` writes to **local** scope unless told otherwise. Local scope is
private to you; project scope writes `.mcp.json` for the team and requires
approval on first use inside `claude`.

```bash
# private to this machine
claude mcp add contexture-demo -- uv run contexture demo

# or shared with the team
claude mcp add --scope project contexture-demo -- uv run contexture demo
```

Check it:

```bash
claude mcp list          # expect: contexture-demo … ✔ Connected
claude mcp get contexture-demo
```

Inside `claude`, `/mcp` shows the same.

## Drive it

Restricting `--allowed-tools` to the server's own tools is the point: it proves
the answer came over MCP and not from reading this repository.

```bash
claude -p "Use the contexture-demo MCP server to diagnose why pod \
payments-api-7d9c in namespace prod keeps restarting. Start from Contexture's \
disclosed role and skill context. Use MCP evidence instead of inspecting this \
repository's source code. Explain the root cause and the next remediation step." \
  --allowed-tools \
    "mcp__contexture-demo__contexture_discover" \
    "mcp__contexture-demo__contexture_open" \
    "mcp__contexture-demo__contexture_invoke_read_only"
```

## What counts as a pass

- [ ] It opens `kubernetes-platform`, then `incident-response`, then
      `diagnose-crash-loop-backoff`, taking each ref from a disclosed card.
- [ ] It calls status, logs, events, and the `crash_loop_runbook` Tool.
- [ ] Root cause: `DB_URL` missing, container exits 1, not an OOM kill.
- [ ] It refuses to recommend restarting first — a constraint that exists only
      inside the skill's instructions.
- [ ] No repository read, `CLAUDE.md`, `SKILL.md`, or `.claude/skills/**` was
      involved.

Record authentication failures separately from framework failures. The
2026-09-06 candidate attempt was blocked before inference by a revoked OAuth
token; it is not a failed navigation run.

## Clean up

```bash
claude mcp remove contexture-demo
```
