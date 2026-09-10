# Verify with Claude Code

## Current binding-candidate result

On 2026-09-10, Claude Code 2.1.133 passed this diagnosis against both the
TypeScript and Go 0.12 candidates. Each run used isolated MCP configuration,
disabled every built-in tool, allowed only the four current Contexture gateway
Tools, completed in seven model turns, and returned no permission denial.

Both runs collected status, previous logs, events, and the runbook; identified
missing `DB_URL`; cited exit code 1 rather than 137; recommended repairing the
ConfigMap or Secret before rollout; and rejected a blind restart. The Go run
passed after commit `23cc42f` fixed two defects found by the real Host. See
[hosts.md](hosts.md) and the binding-specific verification records for exact
candidate commands.

The revoked-OAuth attempt described below is the archived Python candidate
record from 2026-09-06, not the current binding result.

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
archived 2026-09-06 Python candidate attempt was blocked before inference by a
revoked OAuth token; it is not a failed navigation run.

## Clean up

```bash
claude mcp remove contexture-demo
```
