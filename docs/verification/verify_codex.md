# Verify with Codex

The point of this file is that it differs from `verify_claude_code.md` only in
the CLI syntax. Same server, same launch command, no Codex-specific artifacts.

```bash
uv sync --extra dev
codex mcp add contexture-demo -- uv run contexture demo
codex mcp list          # expect: contexture-demo … enabled
```

Inside `codex`, `/mcp` shows the same.

## Drive it

```bash
codex exec --ephemeral -s read-only "Use only the contexture-demo MCP server \
to diagnose why pod \
payments-api-7d9c in namespace prod keeps restarting. Start from Contexture's \
disclosed Role and Skill context. Do not inspect repository files or use shell \
commands. Call the disclosed evidence tools and the crash_loop_runbook Tool. \
Explain the root cause, cite the exit code, and give the smallest safe next action."
```

## What counts as a pass

Identical to the Claude Code checklist, plus the thing this row exists to show:

- [ ] The launch command is the same string used for Claude Code.
- [ ] The call trace contains only Contexture MCP calls and agent messages, not
      shell or repository-read calls.
- [ ] It follows status → logs → events → runbook and cites exit code 1.

The automated instruction-budget tests remain the source of truth for bootstrap
length; this manual run verifies whether the current client actually navigates.

## Clean up

```bash
codex mcp remove contexture-demo
```

## Status

The binding-candidate attempt on 2026-09-10 used Codex CLI 0.153.0, but
`codex login status` returned `Not logged in`. It was blocked before inference,
so no binding pass or product failure is claimed. Repeat after a maintainer
authenticates directly in the terminal; never store the token in repository
configuration or verification output.

The completed 2026-09-06 Codex CLI 0.153.4 run against Python candidate commit
`16dacc9` remains valid historical evidence: eight MCP calls, zero errors, and
the correct diagnosis. It is not the current TypeScript/Go candidate result.
See [hosts.md](hosts.md).
