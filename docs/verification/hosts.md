# Host verification

# TypeScript and Go 0.12 candidates — current four-tool gateway

Recorded 2026-09-10 with Claude Code 2.1.133. TypeScript was verified against
commit `dec12f5`; Go passed after the Host-discovered fixes in `23cc42f`.

| Binding | Client | Result |
| --- | --- | --- |
| TypeScript | Claude Code 2.1.133 | passed: seven-turn MCP-only diagnosis, zero permission denials |
| Go | Claude Code 2.1.133 | passed: seven-turn MCP-only diagnosis after two protocol fixes |
| TypeScript and Go | Codex CLI 0.153.0 | blocked before inference: local account not logged in |
| TypeScript | Official MCP client 2.0.0 | passed in the automated suite |
| Go | Official MCP client 1.7.0 | passed in the automated suite |

Both Claude runs used isolated MCP configuration from outside the repository.
All built-in tools were disabled; only the server's four current Contexture
gateway Tools were available. Each run collected Pod status, previous logs,
events, and `crash_loop_runbook`, then identified missing `DB_URL`, cited exit
code 1 rather than OOM/137, recommended repairing the projected ConfigMap or
Secret before rollout, and refused a blind restart.

The first Go attempts exposed two real defects before the passing run:

1. revalidating default stdio options treated effective host, port, and path
  defaults as explicitly stated HTTP fields;
2. scalar and array Tool values were placed directly in `structuredContent`,
  where MCP requires an object.

Go commit `23cc42f` made option validation idempotent and normalized structured
results to objects, with focused official-SDK regression tests. Full
conformance, race-enabled tests, external-module consumption, and vet passed
afterward.

Codex CLI was available through the pinned ephemeral npm package, but
`codex login status` returned `Not logged in`. No Codex model request was made;
this is an external account blocker, not a product pass or failure. Repeat that
row only after a maintainer authenticates directly in the terminal.

Binding-specific reproduction records live in each binding repository under
`docs/verification/`. Request-selected HTTP surfaces and Prompt-only roots
remain covered by official-client integration tests because this diagnosis is
a stdio model-navigation scenario.

---

# Archived Python 0.12.0rc1 candidate record

Recorded 2026-09-06 against commit `16dacc9` on branch `release/0.12.0`.

| Client | Version | Result |
| --- | --- | --- |
| Codex CLI | 0.153.4 | passed: eight MCP calls, zero errors |
| Claude Code | 2.1.158 | blocked before inference: OAuth token revoked (401) |
| Official Python MCP client | 2.1.1 | passed in the automated suite |

The Codex run registered only this server, asked the model not to inspect the
repository or use shell commands, and used the current Demo Skill instruction
that calls the content Tool rather than the removed `contexture_read` gateway.

```text
1. contexture_discover
2. contexture_open  kubernetes-platform
3. contexture_open  kubernetes-platform/incident-response
4. contexture_open  .../diagnose-crash-loop-backoff
5. contexture_invoke_read_only  .../get_pod_status
6. contexture_invoke_read_only  .../get_pod_logs
7. contexture_invoke_read_only  .../get_pod_events
8. contexture_invoke_read_only  .../crash_loop_runbook
```

The model followed all four procedure steps in order. It concluded that
`DB_URL` is missing, cited `CrashLoopBackOff`, 14 restarts, `ready: false`, and
exit code 1, distinguished that from an OOM/137 failure, and recommended adding
the key to the projected ConfigMap or Secret before rolling out. It explicitly
refused a blind Pod restart. The JSONL trace contains only MCP tool calls and
agent messages; no shell or repository-read call occurred.

The temporary Codex MCP registration was removed immediately after the run.
Claude Code accepted the isolated MCP configuration but returned `401 OAuth
access token has been revoked` before any model turn, at zero cost. That row
must be repeated after the maintainer restores Claude authentication; it is not
reported as a product failure or a pass.

This run verifies current four-tool discovery, one-level Role navigation,
Skill disclosure, schema-driven read-only invocation, and content-as-Tool. It
does not provide model-level evidence for Prompt-only roots or request-selected
HTTP roots; those remain covered by protocol integration tests and should be
included in a future suitable Host scenario.

---

# Archived pre-v0.4 records

The records below are preserved as historical evidence. They show a five-tool
gateway in which `contexture_read` returned Resource content. Since ADR 009 the
gateway has four tools, content is an argument-free read-only Tool, and a URI
is served on MCP's native Resource primitive. Their payload shapes are not the
current contract.

---

# v0.2.0 + ADR 007 — one level per call

Recorded 2026-08-19 against `b43b0bd` plus the roster fix below, on the same
machine as the v0.2.0 record.

ADR 007 changed the first step of the run recorded underneath this one, so that
run is now a record of a navigation model that no longer exists. Two runs were
taken: the same prompt against the demo, and a new one against a tree deep and
wide enough to exercise what ADR 007 was written for.

## Run 1 — the demo, same prompt as the v0.2.0 record

**The predicted extra hop did not happen.** ADR 007's handoff expected
`discover → open(root) → open(role) → open(skill)` where the earlier run had
`discover → open(role) → open(skill)`. What the host actually did was skip
`discover` altogether:

```text
1. contexture_open           kubernetes-platform/incident-response
2. contexture_invoke_read_only  .../get_pod_status
3. contexture_open           .../diagnose-crash-loop-backoff
4. contexture_invoke_read_only  .../get_pod_logs   {previous: true}
5. contexture_invoke_read_only  .../get_pod_events
6. contexture_read           contexture://runbooks/crash-loop-backoff
```

Seven calls in eight turns, zero errors — one call *fewer* than before the
change. The reason is that `contexture_discover` and the bootstrap roster no
longer describe the same model: `discover` answers with the roots, while
`instructions.build()` keeps listing while it has budget, so the demo's whole
two-level forest is in the opening text. The host already had
`kubernetes-platform/incident-response` before its first call and went straight
there.

That is the roster doing its job, and it means **a small forest cannot be used
to test ADR 007 at all** — the opening text shortcuts the traversal being
measured.

## Run 2 — four levels, with the roster deliberately cut

A synthetic platform tree: 26 roles, four levels deep, five roots. The roster
budget cuts it after seventeen entries, and the cut lands above the branch that
matters — `payments/ledger` is named, `payments/ledger/settlement` and
everything under it is not. The fact the task needs sits at level four, so the
opening text cannot shortcut anything.

```text
1. contexture_discover
2. contexture_open           payments
3. contexture_open           payments/ledger
4. contexture_open           payments/ledger/settlement
5. contexture_open           payments/ledger/settlement/reconciliation
6. contexture_invoke_read_only  .../get_reconciliation_gap  {ledger_date}
```

**Seven calls, eight turns, zero errors.** One `open` per level, each ref taken
from the card the previous call returned. It never assembled a ref, never took
a wrong branch, and never guessed at a sibling it had not been shown. It also
called `discover` here, having skipped it in run 1 — the roster's truncation
line is the only difference between the two situations.

It closed by naming what it could not answer — the tool returns an aggregate,
not the three underlying entries — and proposed which sibling branches might
hold a tool that could. That is the same boundary-respecting behaviour the
v0.2.0 record noted, at three times the depth.

**What this settles:** depth-four traversal works, a cut roster is navigated
rather than guessed around, and the cost is one call per level — linear in
depth, as designed. Seven calls at four levels against seven at two: the extra
hops replaced the work the roster used to do for free.

**What it does not:** `contract.unresolved()` still has no field evidence. Two
hosts, three runs, and not one wrong ref.

## The defect these runs found

Building the truncating tree exposed a bug in the roster rather than in the
traversal. The budget was spent entry by entry, so it could stop *inside* one
parent's children: at 8×3, `r2` appeared with three of its eight sub-roles and
nothing said the group was cut. Three of eight look like the whole choice,
which is the guess ADR 004 stated the rule against and ADR 007 kept. The budget
is now spent in whole sibling groups, and the roots — the one group a single
named call restores, since `discover` answers with exactly them — are cut entry
by entry with a line that names that call. `RosterTests` in
`tests/test_projection.py` holds all of it.

---

# v0.2.0 — the five-tool gateway

Recorded 2026-08-19 against `57eb59b`, on a machine that has the SDK, `uv`,
`node`, both host CLIs, and a network — none of which the machine that wrote
`HANDOFF.md` had.

The claim under test: **a model will choose to navigate a gateway.** Everything
else about the gateway was already mechanically proven by
`tests/test_stdio_server.py`; what no test can prove is that a model does not
simply give up when every capability is hidden behind a generic dispatch tool.

| Host | Version | Connected | Full diagnosis |
| --- | --- | --- | --- |
| Claude Code | 2.1.235 | ✅ | ✅ recorded below |
| Codex | 0.147.0 | ✅ registered | ⚠️ blocked — usage limit until 2026-08-21 |
| Official SDK client | mcp 2.x | ✅ | ✅ automated, `tests/test_stdio_server.py` |

## Claude Code

Run headlessly, with only the five gateway tools allowed, so nothing could be
answered by reading this repository:

```bash
claude -p "<pod payments-api-7d9c in prod is unhealthy; find the root cause \
using the contexture-demo MCP server; do not guess>" \
  --mcp-config mcp.json \
  --allowed-tools "mcp__contexture-demo__contexture_discover,\
mcp__contexture-demo__contexture_open,mcp__contexture-demo__contexture_read,\
mcp__contexture-demo__contexture_invoke_read_only,\
mcp__contexture-demo__contexture_invoke" \
  --output-format stream-json --verbose
```

### The traversal, as it happened

```text
1. contexture_discover
2. contexture_open           kubernetes-platform/incident-response
3. contexture_open           kubernetes-platform/incident-response/diagnose-crash-loop-backoff
4. contexture_invoke_read_only  .../get_pod_status  {namespace, pod}
5. contexture_invoke_read_only  .../get_pod_logs    {namespace, pod, previous: true}
6. contexture_invoke_read_only  .../get_pod_events  {namespace, pod}
7. contexture_read           contexture://runbooks/crash-loop-backoff
```

**Nine turns, eight tool calls, zero errors.** `is_error: false` on every one.

### The four questions HANDOFF item 3 asked

1. **Does the host open a role before reaching for a tool?** Yes, and it opened
   the skill too. Calls 1–3 are pure navigation; the first invoke is call 4.
2. **When it passes a ref that does not resolve, does the sentence get it back
   in one turn?** **Not observed — it never passed a bad ref.** A good outcome
   that leaves the question open. `contract.unresolved()` still has no field
   evidence, and forcing one synthetically would not be that evidence.
   (Legibility on the wire *is* confirmed separately: a wrong ref returns the
   members the role does hold plus the call that recovers, and both wrong-door
   refusals name the other door.)
3. **Does either host truncate the instructions?** No. The demo's full
   instructions are **884 characters** against Claude Code's 2KB, and the
   preamble is **465** against Codex's 512-character self-contained window —
   both re-measured against this commit rather than read off documentation. The
   roster budget does bind: forty roots render to 1806 characters, still inside
   2KB.

### What it did beyond connecting

- **Followed the disclosed procedure in order** — status → logs → events —
  which exists only inside the skill it opened at step 3.
- **Read an optional parameter off the card schema.** It passed
  `previous: true` to `get_pod_logs`; nothing in the prompt suggested it.
- **Addressed the resource by its own URI**, not by its ref — the spelling
  `ContextTree.resource()` accepts so a procedure can name a document the way
  the document names itself.
- **Obeyed the skill's constraint**: it refused to recommend restarting the Pod,
  and said why (restart 15).
- **Respected the disclosure boundary.** It closed by noting it had not opened
  `kubernetes-platform/deployment-ops` and would therefore not assert anything
  about the release history. It declined to guess about a role it had seen a
  card for and not opened, which is the behaviour the whole design is betting
  on.

## Codex

Registration against v0.2.0 succeeds and needs no Codex-specific artifact:

```bash
codex mcp add contexture-demo -- /path/to/.venv/bin/python -m contexture.examples.incident.server
codex mcp list
# contexture-demo  …  enabled  Unsupported
```

The diagnosis run is blocked by the same account limit that blocked it at
v0.0.4:

```text
ERROR: You've hit your usage limit. … try again at Aug 21st, 2026 12:02 PM.
```

The server stays registered, so the run is one command away once the limit
resets. See `verify_codex.md`.

---

# Host verification — v0.0.4

Recorded 2026-08-19 against the branch `feature/native-mcp-server`.

> **This record predates v0.1.0 and is kept as recorded, not updated.** It shows
> the surface as it was: business tools registered natively, and
> `contexture_get_context` where `contexture_open` now stands. What it verifies
> is still the claim under test — one server, one launch command, two hosts, no
> generated context files. A re-run against the five-tool gateway has not been
> recorded yet, and `verify_claude_code.md` drives the current surface.

The claim under test: **one server, one launch command, two hosts, no generated
context files.**

| Host | Version | Connected | Full diagnosis |
| --- | --- | --- | --- |
| Claude Code | 2.1.235 | ✅ | ✅ recorded below |
| Codex | 0.147.0 | ✅ registered | ⚠️ blocked — see below |
| Official SDK client | mcp 2.0.0 | ✅ | ✅ automated, `tests/test_stdio_server.py` |

## Claude Code

```bash
claude mcp add contexture-demo -- /path/to/.venv/bin/contexture demo
claude mcp list
# contexture-demo: … - ✔ Connected
```

Run headlessly with only the server's own tools allowed, so nothing could be
answered by reading this repository:

```bash
claude -p "Use the contexture-demo MCP server to diagnose why pod \
payments-api-7d9c in namespace prod keeps restarting. Start from Contexture's \
disclosed role and skill context. Use MCP evidence instead of inspecting this \
repository's source code. Explain the root cause and the next remediation step." \
  --allowedTools \
    "mcp__contexture-demo__contexture_discover" \
    "mcp__contexture-demo__contexture_get_context" \
    "mcp__contexture-demo__get_pod_status" \
    "mcp__contexture-demo__get_pod_logs" \
    "mcp__contexture-demo__get_pod_events" \
    "ReadMcpResource"
```

Result: 12 turns, no permission denials, `is_error: false`.

What the run demonstrates, point by point:

* It **found the role and skill through discovery**, not through the filesystem —
  it names `kubernetes-incident-responder` and `diagnose-crash-loop-backoff`,
  which appear nowhere except in the server's responses.
* It **followed the disclosed procedure in order** (status → logs → events →
  runbook), which only reached it via `contexture_get_context`.
* It **read the resource** rather than being handed its content up front.
* It **obeyed the constraint** that exists only inside the skill: it explicitly
  refused to recommend restarting the Pod first.
* It **distinguished exit 1 from exit 137**, which is the runbook's own test for
  separating a config failure from an OOM kill.

### Transcript

```text
## Diagnosis: `prod/payments-api-7d9c`

I started from the server's disclosed role (`kubernetes-incident-responder`), took its `diagnose-crash-loop-backoff` skill, and followed that procedure in order: status → logs → events → runbook.

**Evidence collected**

| Source | Finding |
| --- | --- |
| `get_pod_status` | `phase: Running`, `container_state: CrashLoopBackOff`, `restart_count: 14`, `ready: false`, image `registry.internal/payments-api:1.8.2` |
| `get_pod_logs` | `ERROR ConfigurationError: required environment variable DB_URL is missing`, followed by `FATAL startup aborted after configuration error` |
| `get_pod_events` | `Created container payments-api` ×15, `BackOff: Back-off restarting failed container` ×14, `Unhealthy: Container exited with code 1` ×14 |

**Root cause**

The container is missing the required environment variable `DB_URL`. The application reads its configuration at startup, finds `DB_URL` absent, logs `ConfigurationError`, and aborts before serving anything. It exits with **code 1** — an application-level self-rejection, not a kill. Per the runbook, exit 1 is what separates this from an OOM kill (which would be exit 137 and a different runbook), so memory limits are not implicated. The image pulls and the container is created successfully all 15 times, which rules out image or scheduling problems: the failure is entirely inside startup config loading.

Matching against the runbook's cause table, this is row one: *`ConfigurationError`, missing variable → Config or Secret not projected into the Pod.*

**Next remediation step**

Add the `DB_URL` key to the ConfigMap or Secret referenced by the `payments-api` Pod spec (and confirm the spec actually projects that key into the container as an env var), then roll out the Deployment.

Do not restart or delete the Pod first. The 14 restarts already performed are the same operation the kubelet is retrying automatically; a manual restart repairs nothing and just produces restart 15.

One scope note: the MCP tools here expose status, logs, and events only — there's no tool to read the ConfigMap/Secret or the Pod spec, so I can't tell you from evidence *which* object is missing the key or whether it's an absent key versus a broken `valueFrom` reference. That distinction needs a look at the workload manifest.
```

## Codex

Registration succeeded and the server is listed:

```bash
codex mcp add contexture-demo -- /path/to/.venv/bin/contexture demo
codex mcp list
# contexture-demo  …/contexture demo  -  -  -  enabled  Unsupported
```

The diagnosis run could **not** be completed:

```text
ERROR: You've hit your usage limit. … try again at Aug 21st, 2026 12:02 PM.
```

So **Codex is unverified end to end.** What is verified is that it accepts the
same launch command with no Codex-specific artifacts. The protocol path itself
is covered independently by `tests/test_stdio_server.py`, which drives the same
subprocess with the official SDK client over real stdio — including the legacy
handshake revision Codex may negotiate — but that is not a substitute for
running Codex itself.

To finish this row once the limit resets, see `verify_codex.md`.

## Automated equivalent

`tests/test_stdio_server.py` launches the demo as a subprocess and drives it
over real stdio with the official client, asserting:

* the 2026-07-28 revision is negotiated through `server/discover`
* a legacy host using `initialize` still gets a working server
* server instructions fit both hosts' limits (512-char opening, 2KB total)
* `read_only` appears as an annotation and in no input schema
* the full traversal produces the intended evidence
* a skill's procedure is absent from bootstrap and from every routing card, and
  present only after `contexture_get_context`
* resource content is absent from listing and from disclosure, and present only
  after a read
