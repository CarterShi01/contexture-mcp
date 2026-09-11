# ADR 008 — Who may open a node


> **Partly superseded by [ADR 013](013-a-constructor-is-the-declaration.md).**
> Every reference below to `declarative.collect`, `_materialize`, or an object
> graph built at import time describes machinery that no longer exists: a class
> body is not read, and nothing is constructed until a `ControllerManager`
> registers a root. The decision this ADR records — who may open a node, and
> why a reference overlay may contain cycles — is unchanged.
**Status:** accepted, implemented in v0.3.0
**Date:** 2026-08-20

**Extends:** [ADR 004](004-progressive-disclosure-as-a-lazy-role-tree.md) and
[ADR 007](007-the-role-axis-is-lazy-too.md). Neither is contradicted. Both
describe how a *model* reaches a capability. This one is about how a *person*
does, which is a plane those ADRs never had to name.

## Context

### The surface is entirely model-controlled

Everything Contexture serves sits behind five gateway tools, and a model
chooses among them. `docs/verification/hosts.md` records that holding across
three runs: the host navigates rather than giving up, one `open` per level, no
assembled refs, no guessed siblings.

Two things that surface cannot express.

**A person who already knows the destination has no way in.** Every entrance is
a decision the model makes.

**A workflow spanning unrelated branches has no home.** `Skill` holds nothing —
`declarative.collect(cls, member_types=())` — and its docstring settles the
matter: *"A method that needs its own tools to be kept away from its siblings'
tools is a child Role, not a Skill."* That answer is containment, and
containment is exclusive here because a ref **is** a path and a node has
exactly one. A procedure naming `assets/image-gen/generate_cover` and
`distribution/newsletter/send_draft` cannot own either; each already belongs
somewhere else. The model can state **ownership**. It cannot state
**reference**.

A related smell is already visible: demo skill procedures name sibling tools by
bare name (`Call get_pod_status`). That works only because those cards arrive
from the same `open(role)`. It stops working the moment a procedure names
anything outside its own parent.

### MCP's third primitive is the unused half

The three primitives are split by *who decides*. The 2026-07-28 revision added
a sentence the 2025-06-18 text does not contain:

> Prompts are designed to be **user-controlled**, meaning they are exposed from
> servers to clients with the intention of the user being able to explicitly
> select them for use. **This refers to who decides when the prompt is used,
> not who authors its content. Prompt content is defined by the server.**

The spec authors hit the same ambiguity this ADR had to resolve and settled it
the same way: the axis is *who triggers*, not identity, authorship, or content.

Verified against the installed SDK (`mcp 2.0.0`, revision `2026-07-28`) rather
than from memory: `prompts/get` takes `{name, arguments: dict[str,str]}` and
answers `{messages: [PromptMessage{role, content}]}`;
`completion/complete` takes `ref: PromptReference | ResourceTemplateReference`
plus `argument: {name, value}` and answers `Completion{values (max 100), total,
hasMore}`; `PromptListChangedNotification` is delivered **only** on a
`subscriptions/listen` stream the client opted into via `promptsListChanged`.
`prompts/list` results **MUST NOT** vary per-connection or as a side effect of
another request.

Host-local command files (`.claude/commands/*.md`, `~/.codex/prompts/*.md`) are
the obvious alternative and are rejected: they are generated artifacts in the
user's repository, which is what `hosts.md` records this project as not needing.

### A command is not a new kind of thing — the industry already merged them

Claude Code's documentation states it outright:

> **Custom commands have been merged into skills.** A file at
> `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md`
> both create `/deploy` and work the same way.

What distinguishes them is two frontmatter fields, and nothing else:

| Frontmatter | You can invoke | Claude can invoke | When loaded into context |
| --- | --- | --- | --- |
| (default) | Yes | Yes | Description always in context, full skill loads when invoked |
| `disable-model-invocation: true` | Yes | No | **Description not in context**, full loads when you invoke |
| `user-invocable: false` | No | Yes | Description always in context, full loads when invoked |

The third column is a context bill, which is this project's own currency. The
stated criterion for reaching for it — *"Use this for workflows with side
effects or that you want to control timing, like `/commit`, `/deploy` … You
don't want Claude deciding to deploy because your code looks ready"* — is a
guardrail argument, not a convenience one.

Contexture already occupies exactly one row of that table: every node today is
model-openable and not person-openable. **This ADR does not introduce a
concept; it finishes a table the project already sits in one cell of.**

Provenance, stated honestly: those two fields are **Claude Code extensions**,
not the Agent Skills open standard, which admits only `name`, `description`,
`license`, `compatibility`, `metadata`, `allowed-tools`. The semantics are
well-founded — the largest client behaves this way and MCP's `user-controlled`
agrees — but they carry no open-standard backing, so this ADR borrows the
meaning and not the spelling.

### The three-stage disclosure of Agent Skills

The open standard describes progressive disclosure in three stages: discovery
(name and description only), activation (full `SKILL.md`), execution
(*"optionally executing bundled code or loading referenced files as needed"*).

`core/context.py` argues there are two levels and no third. Both are right.
Stage three is not a third level of one node — it is the skill reaching
*other* nodes, which here is `uses` rendering cards that are opened or read by
their own separate calls. **Contexture's two levels across many nodes is the
same thing as three stages across one file, decomposed better:** the third
stage is another node, not another level.

### What the numbers say

Measured against the demo and a synthetic forest; JSON characters, not tokens.

Signpost ancestry against the payload it decorates:

| | demo | synthetic |
| --- | ---: | ---: |
| full ancestry (replay `open` at each level) | +206% | +724% |
| signpost (path + sibling counts, names only) | **+13%** | **+56%** |

Full ancestry is disqualified: it re-buys every level a direct hit just saved.
The signpost cost scales with **depth**, never with breadth — eight siblings
and three siblings render the same line.

A 300-role multi-headed forest with distinct strings is **184 KB** resident;
`find()` on the deepest ref is ~23µs. Server memory is cheap and unshared;
model context is expensive and is the bottleneck. Keep making the expensive one
lazy and carry the cheap one.

**A correction to an earlier draft of this ADR.** It claimed a direct hit saves
five navigation calls (63–81%). That assumed the model must *discover* the
path. A direct hit's premise is that the person already knows the ref — and a
person who knows it can simply say it, which costs one `open`. The honest
saving is **two calls to one**, plus completion so the person need not
reproduce the ref exactly. Completion is the value; round trips are not.

### A defect the measurements appeared to surface, and did not

`ContextTree.skeleton()` — what `contexture_discover` returns — has no budget
and no truncation, while every other budget in the project cuts and names the
call that restores what was cut. That reads like an oversight, and an earlier
draft of this ADR recorded it as one and scheduled a fix.

It is a floor, not an oversight, and implementing the fix is what showed it.
The roster's own truncation line reads *"call `contexture_discover` for the
**complete list**"*, so capping `discover` turns that sentence into a lie with
nothing left to point at. And the roots are one sibling set, which puts a cap
straight into ADR 004: *a choice made among a subset of the alternatives is a
guess rather than a choice*. Capping the entrance would buy characters by
reintroducing the exact failure the disclosure model exists to remove.

**`contexture_discover` is the one call that must always answer in full,
because it is what every other truncation recovers to.** Pinned by a test, so
that anyone tempted to add a budget has to change the roster's promise first.

## Decision

**Progressive disclosure is unchanged. What grows is the set of who may
trigger it.** Two fields; no new object, no new axis, no new compile level.

| Where | Field | What it states |
| --- | --- | --- |
| `core/context.py` → `ContextNode` | `opened_by` | who may open this node |
| `core/skill.py` → `Skill` | `uses` | refs this procedure names but does not own |

### 1. `opened_by` belongs on `ContextNode`

`context.py` states the base class owns *"a machine-facing name, a routing
description, and a compile lifecycle."* Who may trigger a compile is part of
that lifecycle, so it belongs there rather than on one subclass. All four kinds
have a meaningful reading: a person-opened Skill is `/do`; a person-opened Tool
is `/deploy`; a person-opened Role or Resource is a named entrance to a branch
or a document.

```python
opened_by = (Opener.MODEL,)                 # default — today's behaviour
opened_by = (Opener.PERSON,)                # a command; the model may not enter
opened_by = (Opener.MODEL, Opener.PERSON)   # both planes
```

Empty is refused: a node nobody can open is not a node.

**The default is `(MODEL,)`, which is the opposite of Claude Code's, and the
inversion is forced.** Claude Code has a handful of skills in a flat directory;
Contexture has hundreds of nodes in a tree. Defaulting to both planes would
make the command surface grow with the forest, which decision 3 forbids.

### 2. It is `open`, never `invoke`

`skill.py` draws the line: *"a Tool is executed by the framework and returns a
result, a Skill is executed by the model and returns nothing."* A Skill has no
executable body. It can be **opened**; it cannot be **invoked**. A prompt
naming a skill means *"put its procedure in context, because a person asked"* —
that is `open`, and the field is named for it. Calling it invocation would
blur the contract the two invoke gateways rest on.

### 3. The command surface must not scale with the tree

`prompts/list` holds one entry per node marked `PERSON`, plus `goto`. That
count is **authored, not derived**: eight commands is eight entries whether the
forest holds thirty nodes or three hundred. Adding a command is a code change
and a restart — a new server version, not a surface varying under a live
connection.

The criterion for marking a node, borrowed from one-creator's ADR 0093 and
recorded here because without it `PERSON` gets sprinkled everywhere until the
menu is a second tool list:

> A command is worth having only where **going wrong is expensive**. Its value
> is consistent execution, guardrails, and saved typing — and saved typing is
> the weakest of the three. Anything else is better served by simply talking to
> the agent.

### 4. A person-only node keeps its card, and `open` refuses it

The card stays in `open(role)` so the model knows the capability exists and can
tell the person which command reaches it. `contexture_open` on it is refused,
and the refusal names the command — the pattern `contract.wrong_door()` already
establishes, where *both wrong-door refusals name the other door*.

The card carries `opened_by` whenever it is not the default, so the model can
avoid the refused call rather than discover it by making it.

This costs the card's context, which the third column of Claude Code's table
does not pay. Accepted deliberately: a model that cannot see the capability
cannot point a person at it, and pointing is what makes a guardrail cooperative
rather than merely obstructive.

### 5. `uses` belongs on `Skill` alone

Not on `ContextNode`. A Role reaches by containment, a Tool has its own code, a
Resource has its own content; only a Skill is *"a procedure whose steps are
existing tools, with no code of its own to run."* The asymmetry against
`opened_by` is principled: every node is opened by someone, but only a
procedure names things it does not own.

**Refs are strings, never object references.** Object references would make the
forest a graph and `_reject_cycles` meaningless. The type *is* the distinction:
a `tuple[str, ...]` cannot be walked into, so the containment walkers cannot
follow it by accident. Containment is **down**; reference is **sideways**. A
skill stays a leaf, and `uses` produces no depth, no children, and no new ref.

### 6. A person-openable node still lives in the tree

A skill floating free of every role has no owner, and *whose responsibility is
this* is the framework's whole subject. It does not need to float: a command's
name is a **position-independent second name**, exactly as `Resource.uri` is
one. `compose-and-ship` lives at `one-creator/publishing/compose-and-ship` and
is `/compose-and-ship` at the command line, the same way a runbook has both a
ref and a URI and `contexture_read` accepts either.

### 7. `uses` may not point at a Role — for now

Evidence over taste: one-creator's `/do` entrypoint declares five operations
and one resource in its `requirements` block and **no role at all**. Its
cross-role routing reads a live resource (`team://surface/{project}`) and acts
on what it names. So dynamic routing is solved by a resource, not by reference
syntax, and restricting `uses` to leaves keeps the reference overlay from
tangling with the containment forest. Relax it when something genuinely cannot
be expressed.

### 8. Reference cycles are allowed; deadlock is impossible

Two graphs, and only one is structural:

| | containment | reference (`uses`) |
| --- | --- | --- |
| held as | object pointers | ref strings |
| walked by | `_reject_cycles`, `nodes_with_refs`, `roles_by_level` | nothing |
| shape | forest, enforced | arbitrary graph, allowed |
| creates addresses | yes | no |

Deadlock needs blocking plus circular wait; there is neither. Every gateway
call renders **one level** and returns, which is `open`'s existing rule:
*"Opening a role delivers that role's members and does not recurse into
sub-roles."* `uses` obeys it too — it expands to ROUTE cards, and a ROUTE card
carries `{kind, name, description, ref}` and never its own `uses`. The server
never follows a reference transitively. `A → B → A` means two pages linking to
each other, not a cycle to traverse.

Forbidding reference cycles would be over-strict: `diagnose → remediate →
diagnose` is a legitimate workflow shape.

## Three invariants

Written down because each is load-bearing and none is self-evident from the
code that depends on it.

1. **`uses` expands to ROUTE only, never ACTIVE.** This is the whole reason
   cycles are harmless.
2. **The roster, completion, and every enumerator walk containment only, never
   `uses`.** This is the one place in the design where a reference cycle
   *could* become an infinite loop, and it is on the startup path.
3. **Completion cuts by relevance; the roster cuts in whole sibling groups.**
   The roster's rule exists because a model reading three of eight takes three
   for the whole choice. Completion's consumer is a person, who keeps typing.
   Same protocol, different consumer, deliberately different rule.

## Build-time validation

Beside `_reject_cycles` and `_reject_ambiguous_names` in
`ContextTree.__post_init__`, because a workflow naming a node that does not
exist must fail at startup and not when somebody presses the key.

| Check | Verdict |
| --- | --- |
| every `uses` ref resolves | reject |
| self-reference | reject |
| reference to an ancestor | reject — it is already inside it |
| `uses` naming a Role | reject (decision 7) |
| `opened_by` empty | reject |
| cycle in the reference graph | allow (decision 8) |
| reference crossing root branches | allow, and **report it** |

The last row is the only new idea. A command crossing a responsibility boundary
is a person composing, not a model guessing, so ADR 004's rule does not forbid
it — but it should be **visible rather than silent**, and that is the strongest
argument for orchestration living in the tree as a declared object rather than
as prose in a host-side template: crossings become reviewable, testable, and
lintable.

## Consequences

- `core/context.py` gains `Opener` and `opened_by`; the compile lifecycle is
  otherwise untouched.
- `core/skill.py` gains `uses`.
- `core/role.py`, `core/tools.py`, `core/resources.py`: untouched.
- `tree.py` gains `nodes_with_refs()` (containment-only, the source for both
  validation and completion), `signpost()`, `uses` card rendering inside
  `open()`, and the validation table above. Cards are assembled here rather
  than in `Skill._compile_active` because a card needs a ref and a schema and
  `core` may know neither — the line ADR 004 drew.
- `server/contract.py` owns every string these prompts put in front of a person
  or a model, beside `GATEWAY`.
- `server/projection.py` registers prompts and the completion handler, and is
  where a person-only node's `open` is refused — the projection layer is the
  only one that knows which door a call came through, exactly as with
  `wrong_door`.
- `server/instructions.py` unchanged. `PREAMBLE` is bound by Codex's
  512-character window, and a model cannot press a command; naming one there is
  pure cost.

## Implementation plan

Three phases, each independently shippable and independently revertable.

**Phase 1 — reference.** `uses` on `Skill`, `nodes_with_refs()`, the validation
table, `uses` cards in `open()`. Pure object model; nothing on the wire
changes. A cross-branch procedure becomes writable and the model reaches it by
ordinary navigation.

**Phase 2 — the person's plane.** `Opener` and `opened_by`, prompt projection,
the completion handler, `signpost()`, and the refusal for a person-only node
opened by a model.

**Phase 3 — the generic entrance.** `goto` with completion.

`goto` earns its place on a value the criterion in decision 3 does not cover.
Consistent execution and guardrails belong to a declared command; saved typing
is the weak leg, and a person who knows a ref can simply say it and spend one
`open`. What nothing else in the design offers is **seeing what the server
holds without spending a model turn** — and unlike a repository, this tree
belongs to somebody else and the person may never have laid eyes on it. That is
browsing, which is what MCP's completion API is for.

`skeleton()` truncation was also planned for this phase and is **not done**,
because attempting it showed it was never a defect. See above.

## Open questions

None blocking. Two to revisit with evidence rather than argument:

1. Whether `uses` should ever name a Role (decision 7 says no on one
   production data point).
2. Whether `Tool` needs `opened_by` in practice, given `read_only` plus host
   approval already gates dangerous calls at the moment of the call. The field
   is available on every node; whether anyone should use it on a Tool is a
   separate question from whether it exists.

## Not done here

- **Codex is unverified.** `hosts.md` records its `mcp list` column as
  `Unsupported`, and its diagnosis run was blocked by an account limit through
  2026-08-21. Whether Codex renders MCP prompts or services
  `completion/complete` is unknown. Claude Code renders prompts as slash
  commands.
- **Multi-tenancy is out of scope and would break the memory model.** The
  object graph is built at *import* time — `Role.__init_subclass__` runs
  `declarative.collect`, whose `_materialize` calls `value()` on each declared
  member class and shares the instance across every instance of the declaring
  class. `declaration` is a `ClassVar`, so the graph is process-wide,
  immutable, and shared. That is what makes `Dispatch._derived`'s `id()` key
  safe and the server lock-free. A per-connection tree would need a process per
  tenant.
