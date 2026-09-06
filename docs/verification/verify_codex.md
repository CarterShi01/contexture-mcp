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

Completed on 2026-09-06 with Codex CLI 0.153.4 against candidate commit
`16dacc9`: eight MCP calls, zero errors, correct diagnosis. See
`docs/verification/hosts.md`.
