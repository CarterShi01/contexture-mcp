# Incident demo

A Contexture MCP server with one deterministic Kubernetes incident. Claude Code
and Codex connect to it with the same command and get the same capabilities;
neither reads a generated `CLAUDE.md`, `AGENTS.md`, or `SKILL.md`.

## What it declares

```text
kubernetes-platform                              role   the coordinator
├── incident-response                            role
│   ├── diagnose-crash-loop-backoff              skill  procedure, on request
│   ├── get_pod_status                           tool   read-only
│   ├── get_pod_logs                             tool   read-only
│   ├── get_pod_events                           tool   read-only
│   └── crash_loop_runbook                       tool   read-only; also a Resource URI
└── deployment-ops                               role
    ├── roll-back-a-failed-release               skill  procedure, on request
    ├── get_rollout_status                       tool   read-only
    ├── roll_back_deployment                     tool   writes; needs approval
    └── rollback_policy                          tool   read-only; also a Resource URI
```

Three roles rather than one, because one role is the shape at which a gateway
surface is a bad trade: a session pays only for the branch it enters, and a
tree with one branch has nothing to not pay for.

## What a host actually sees

Four tools, and no Kubernetes anywhere in them:

```text
contexture_discover · contexture_open
contexture_invoke_read_only · contexture_invoke
```

Everything above — every name, description and schema — arrives inside a
payload, when the role holding it is opened. A session that only diagnoses
never pays for `deployment-ops`.

The roster is in the server's instructions, so a host knows what this server is
for before it calls anything.

## The two doors

`roll_back_deployment` is the demo's one destructive path, and it is why there
are two invoke tools rather than one. It is reached through `contexture_invoke`,
which carries no read-only hint, so a host can put a human in front of it.
Sending it to `contexture_invoke_read_only` is refused rather than executed —
otherwise the host's approval decision would have been made about a call it
could not see.

The incident is fixed: `payments-api-7d9c` in `prod` is in `CrashLoopBackOff`
with 14 restarts, its logs name a missing `DB_URL`, and its events record exit
code 1. Nothing contacts a cluster, so a failed run means the framework failed
rather than the environment.

The symptom does not reveal the cause. A restart loop looks like a scheduling
or image problem until something reads the logs — which is what forces the
traversal this demo exists to show, instead of rewarding a guess.

## Run it

```bash
uv sync
uv run contexture demo               # serves MCP over stdio; blocks
```

## Connect a host

```bash
# Claude Code — note that `mcp add` writes to local scope unless told otherwise
claude mcp add --scope project contexture-demo -- uv run contexture demo
claude mcp list

# Codex
codex mcp add contexture-demo -- uv run contexture demo
codex mcp list
```

Then ask either one:

> Use the contexture-demo MCP server to diagnose why pod payments-api-7d9c in
> namespace prod keeps restarting. Start from Contexture's disclosed role and
> skill context. Use MCP evidence instead of inspecting this repository's
> source code. Explain the root cause and the next remediation step.

## The trace to expect

```text
contexture_open(kubernetes-platform/incident-response)
                                    → its skills, its three tools with schemas,
                                      and a card for the runbook
contexture_open(…/diagnose-crash-loop-backoff)
                                    → the full procedure, here and only here
contexture_invoke_read_only(…/get_pod_status)
                                    → CrashLoopBackOff, restart_count 14
contexture_invoke_read_only(…/get_pod_logs)
                                    → ConfigurationError: DB_URL is missing
contexture_invoke_read_only(…/get_pod_events)
                                    → container exited with code 1
contexture_invoke_read_only(…/crash_loop_runbook)
                                    → matches row 1 of the cause table
```

`contexture_discover` is not in that list, and it is not missing: it answers
with the **root roles alone** — here a single `kubernetes-platform` card — and
the bootstrap roster in the server's instructions already names the branch, so
the recorded run went straight to it. See
[`docs/verification/hosts.md`](../../docs/verification/hosts.md).

Every step of that trace, and its cost, can be read without a host:

```bash
contexture inspect --all --read --summary
```

The whole tree, every document included, is about 3,600 estimated tokens. The
path above is about 1,860 of them; nothing pays for `deployment-ops`.

The conclusion: the container is not being killed, it is rejecting its own
startup state because `DB_URL` is absent, and exit code 1 rather than 137
separates that from an out-of-memory kill. The remediation is to add the key
and roll out — *not* to restart the Pod, which the skill and the runbook both
rule out.

An agent is free to skip the traversal: a ref that was guessed rather than read
still runs, and nothing here is an authorization boundary. What it cannot do is
skip ahead and still know what to do. The surface carries four tools and no
Kubernetes, so the tool names, their schemas, the procedure, the ordering, and
the "do not restart first" constraint all arrive inside payloads that opening
delivers. Disclosure controls knowledge, not access.
