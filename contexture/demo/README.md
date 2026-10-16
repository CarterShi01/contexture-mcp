# Incident demo

[简体中文](README.zh-CN.md) · [Documentation](../../docs/README.md) · [Project](../../README.md)

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

The two child Roles separate diagnosis from deployment work. They make it
possible to inspect alternatives and open the relevant branch without loading
the other branch's guidance and Tool schemas. This small demo illustrates the
mechanism; it is not evidence that a gateway is more efficient for every application.

## What a host actually sees

Five fixed gateway tools, rather than one top-level MCP tool per Kubernetes action:

```text
contexture_discover
contexture_inspect
contexture_open
contexture_invoke_read_only
contexture_invoke
```

Discovery and inspection return routing cards. Opening a Role reveals local
guidance and one level of member cards, including its Tools' input schemas;
opening a Skill reveals its procedure and references. A diagnosis does not
need to open `deployment-ops` or load that branch's detailed guidance.

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
code 1. Nothing contacts a cluster. The fixed data removes the need for cluster
setup; host authentication, configuration, and model behavior still have their
own failure modes.

The symptom does not reveal the cause. A restart loop looks like a scheduling
or image problem until something reads the logs. The intended diagnosis should
therefore cite the returned evidence rather than guess from the symptom.

## Run it

After [installing the CLI](../../README.md#install), inspect the graph without
a host or model:

```bash
contexture check contexture.demo.role:KubernetesPlatform
contexture list contexture.demo.role:KubernetesPlatform
contexture inspect --target contexture.demo.role:KubernetesPlatform kubernetes-platform/incident-response
```

`check` reports three Roles, two Skills, and seven Tools. To start the bundled
MCP server, use `contexture demo`; it serves stdio and waits for a client, so it
is not a command that prints a diagnosis and exits. When connecting a host,
let the host start this process instead of running another copy manually.
From a source checkout, run `uv sync` and prefix these commands with `uv run`.

## Connect a host

Choose one host. These commands register the globally installed CLI from the
previous step. Claude Code uses project scope; the Codex command updates its
user configuration. Remove only the `contexture-demo` entry when finished.

```bash
# Claude Code — run from the project where you will launch Claude Code
claude mcp add --scope project contexture-demo -- contexture demo
claude mcp list

# Or Codex
codex mcp add contexture-demo -- contexture demo
codex mcp list
```

Then ask either one:

> Use the contexture-demo MCP server to diagnose why pod payments-api-7d9c in
> namespace prod keeps restarting. Start from Contexture's disclosed role and
> skill context. Use MCP evidence instead of inspecting this repository's
> source code. Explain the root cause and the next remediation step.

## The trace to expect

One illustrative path is shown below. A host may first use `contexture_discover`
to find the root, `contexture_open` to enter it, or `contexture_inspect` to
compare branch cards without loading their instructions. Actual model choices
can differ; this is not a newly recorded model run.

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

The bootstrap roster can also help the model locate a branch. The
[historical Host records](../../docs/verification/hosts.md) document specific
earlier versions; they do not establish every current host's behavior.

Inspect the disclosure payloads without a host, using the explicit demo target:

```bash
contexture inspect --target contexture.demo.role:KubernetesPlatform --all
contexture inspect --target contexture.demo.role:KubernetesPlatform --all --summary
```

The summary estimates the size of generated context. It does not measure an
LLM session's token usage, latency, bill, or success rate. Inspection is not
Tool execution; running the diagnosis requires actual read calls. The CLI
`contexture inspect` replays disclosure, including opening the supplied refs;
it is distinct from the routing-only MCP tool `contexture_inspect`.

The conclusion: the container is not being killed, it is rejecting its own
startup state because `DB_URL` is absent, and exit code 1 rather than 137
separates that from an out-of-memory kill. The remediation is to add the key
and roll out — *not* to restart the Pod, which the skill and the runbook both
rule out.

An agent may invoke a known ref without first opening it. The gateway does not
enforce the procedure or guarantee that a model follows the guidance.
The five-tool surface carries navigation and invocation; branch-specific
schemas and the "do not restart first" guidance arrive in disclosure payloads.
Disclosure controls what is returned, not business authorization.

For the broader idea, watch the [two-minute overview](../../docs/overview.md).
Its service-operations animation explains the same framework, but is not a
recording of this Kubernetes fixture or a real model run.
