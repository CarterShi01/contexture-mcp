# Contexture public model

This is the language-neutral contract for Contexture. A binding may use
classes, structs, interfaces, DTOs, or schema objects, but must preserve these
meanings and lifecycle boundaries.

## Application declarations

An Application is a lazy composition root with:

- a non-empty `name`;
- one or more model-visible `roots`;
- zero or more user-controlled `prompt_roots`;
- optional shared Channels;
- optional Prompt and Resource publications.

Constructing or importing an Application creates no node instance, connection,
Index, Disclosure, or server. Each compilation builds fresh node instances,
registers both root sets into one canonical forest, validates all refs, and
freezes the resulting Index.

`roots` and `prompt_roots` differ by audience, not structure. Model discovery
can enter only `roots`. The user-controlled Prompt plane can enter either set.
A Prompt root cannot be reached by guessing its ref through a model-controlled
open or invoke call.

## Nodes

Role, Skill, and Tool are the closed node set.

| Node | Required facts | Meaning |
| --- | --- | --- |
| Role | name, description, instructions, members, optional uses refs, optional publication member | A stable responsibility and containment boundary an agent can enter. |
| Skill | name, description, instructions, optional uses refs | Procedural knowledge followed by the model and not executed by Contexture. |
| Tool | name, description, read-only classification, input contract, invoke body, optional uses refs | A deterministic capability executed by Contexture. |

Names and descriptions are explicit and never inferred from identifiers or
comments. Containment is a forest and gives every node one slash-separated
address. A Role may contain child Roles, Skills, and Tools. `uses` points to an
existing address but creates neither containment nor depth.

Every node has three disclosure levels. ROUTE is its pure routing card:
`kind`, `name`, `description`, and, when registered, canonical `ref`. INSPECT
is a framework-generated, non-activating projection consisting of that card,
one level of pure routing cards for direct members, and pure routing cards for
declared `uses`. ACTIVE contains the type-specific actionable surface.

INSPECT introduces no authored field. It MUST NOT contain Role or Skill
instructions, Tool execution facets or schemas, Publication designation or
finishing contracts, content, invocation results, or recursive expansion.
Batch inspection MUST preserve request order and declaration order, MUST be
atomic, and MUST enforce the same selection and model-visibility boundary as
open. Breadth is multiple requested refs; depth is a later explicit inspection
of refs returned as cards.

A Role exposes complete immediate sibling groups when opened. Reverse
dependencies and whole-graph facts are available only through an explicitly
declared introspection Tool; ordinary disclosure does not reveal unentered
branches.

### Optional Role Publication (0.14 extension)

This is the normative Publication extension accepted for Python 0.14.0 in
[ADR 021](../docs/adr/021-role-publication-and-instruction-composition.md).
It does not establish implementation or cross-language parity; the legacy
fixture/golden inventories remain pinned to 0.12.

Publication MUST be a specialization of Role, not a fourth node kind. It keeps
`kind="role"` and `group="roles"` and the same declaration facts: name,
description, instructions, children, skills, tools, uses, and publication.
Python exports `Publication` from `contexture` and inherits the Role
constructor. Its business constructor authors a procedure and may compose
dedicated capabilities or reference shared Tools through `uses`.

A Role MAY designate one constructed Publication as its optional `publication`
member. Python spells this `publication: Publication | None = None`; a class
or a non-Publication node MUST NOT be accepted in that field. Absence MUST
preserve exact existing ROUTE and ACTIVE output, with no `publication` payload
field, extra instructions, or publication obligations. There is no enabled
flag, no no-op member, and no implicit construction for explicit `None`.

The designated Publication MUST participate in containment traversal after
children and before Skills and Tools. Normal name and cross-kind uniqueness,
shared-instance, cycle, and ref validation MUST apply. It receives its
canonical path, Channels, and runtime Tool Bindings by the ordinary lifecycle.
It MUST NOT be added to `branches()` or treated as an alternative work branch.
Containment MUST NOT implicitly inherit an ancestor's Publication. Ordinary
business-constructor inheritance may supply fresh defaults, but explicit
absence disables them. Nodes MUST be fresh per owner and compilation; shared
services belong in Channels or behind existing `uses` refs.

Business-authored `Role.instructions` MUST remain unchanged. Framework ACTIVE
compilation (`Role._compile_active` in Python) MUST append framework
instructions when the Role has a Publication. There are exactly two instruction
ownership categories: business and framework. The Publication's own procedure
belongs to the former.

The ACTIVE owner MUST include the Publication's ordinary routing card in
`roles`, after child Role cards, and a `publication` STRING equal to that
card's actual ref. It MUST NOT inline the Publication's instructions, Skills,
or Tool schemas. The framework instruction MUST reference that real path and
the existing `contexture_open` gateway (Python's `OPEN_TOOL` constant), require
opening the Publication before finishing the owner's work, and require
following its procedure with the work's results and evidence. It MUST make
clear that opening is not execution or successful publication, and require
accurate reporting of pending approval, blockers, and failures, without
inventing success or bypassing approval. ROUTE output MUST NOT gain this
designation or instruction.

Complete-subtree selection includes a selected owner's Publication. Selecting
the Publication alone MUST expose only its subtree, not its owner or an
owner-finishing obligation. `uses` MUST NOT widen selection. Prompt-only
owners MUST NOT leak their Publications through ordinary model disclosure.
If the current view cannot supply the Publication card, framework instruction
composition MUST refuse rather than reveal a hidden ref, silently omit the
contract, or instruct opening an unavailable member.

A Publication MAY explicitly contain another Publication under the same
acyclic containment rules. Opening a Publication directly MUST NOT introduce
extra framework obligations merely because of its type; only its own explicit
`publication` member triggers the same contract as on any Role.

Publication means durable, reusable results, not public Internet distribution
or automatic user visibility. It is distinct from Prompt/Resource
*publications*, which remain exposure pointers rather than nodes.
The four runtime gateways and Tool-only execution MUST remain unchanged.
Publication introduces no automatic execution, finish handler, `Role.invoke`,
hook, destructor, host-specific runtime, or guarantee of external Agent
compliance. Existing approval and authorization boundaries remain in force.

## Bindings and execution

Every runtime Tool has exactly one Binding derived during compilation. The
Binding owns both the input schema shown to a model and validation/invocation of
the business handler. An implementation may derive it from a Python signature,
an explicit TypeScript schema, a tagged Go struct, or a PHP DTO.

Read-only and writing Tools use distinct gateway doors. Calling through the
wrong door is refused. Business handlers are shared across requests and must be
re-entrant; per-call state belongs in arguments and locals.

During an allowed Tool call, task-local `current_graph`, `current_telemetry`,
and `current_principal` equivalents refer to that exact call. Identity is
framework context, not a model-supplied Tool argument. The application owns all
permission decisions.

## Integration declarations

Channels owns application-wide external dependencies and an optional
open/close lifecycle. Successful open is paired with close; a partially failed
open unwinds resources already acquired. Declaration-only validation does not
open Channels.

Prompt and Resource are exposure publications, not Publication Roles or nodes.
Each points to a node already
held by the canonical Index. A Prompt is user-controlled. A Resource is
host-controlled, has a stable URI, and may be backed only by an argument-free
read-only Tool.

## Compilation kinds

Runtime compilation produces a bound Index, ApplicationRuntime, DisclosureAPI,
ExecutionAPI, five-tool MCP surface, optional Prompts and Resources, and the
Channels lifecycle.

Disclosure-only compilation produces an independent unbound Index and only
DisclosureAPI. It accepts no Channels or Resources, derives no Tool schemas,
and exposes only discover/open plus optional Prompts. Tool nodes in this graph
are structural cards, not callable capabilities. Runtime and disclosure-only
applications never share node instances, Indexes, telemetry, or lifecycle.

## Path-selected views

A SurfaceSelection is an immutable set of exact refs and terminal direct-child
patterns, or the compatibility value “all.” `RootSelection` is its 0.11/0.12
compatibility name. A selector is either an exact Contexture ref such as
`team/notebook-editor`, or a pattern whose only wildcard is the complete final
segment, such as `team/*`. `*` matches exactly one segment and never crosses
`/`; recursive wildcards, partial-segment globs, regular expressions, and
negation are not part of the selector language.

Resolution expands every pattern against one compiled Index, rejects selectors
that match nothing, and reduces overlapping refs to a minimal antichain. Each
resolved ref becomes a surface root and selects its complete containment
subtree. A selected descendant is disclosed directly under its canonical ref;
its ancestors and siblings are not disclosed. Intersection is path-aware and
monotonic: when one selected subtree contains another, the narrower root is the
intersection, and a derived view can never restore excluded capabilities.

A `uses` card is visible only if its target ref is also inside the effective
selection. The same effective selection governs instructions, discover, open,
invoke, Prompts, Resources, completion, errors, and the graph visible during
invocation. The parent of a promoted surface root is not visible through that
graph.

Transport adapters may obtain a requested selection from trusted configuration
or request metadata. The HTTP spelling is `Contexture-Select`; the legacy
`Contexture-Roots` spelling remains compatible, and a request must not send
both. Caller input only attenuates. An implementation may intersect it with an
application-owned ceiling derived from verified identity; selection itself is
not authorization.

## Host surfaces

The MCP model-controlled surface is a fixed gateway: discover, batch inspect,
open, read-only invoke, and writing invoke. Business Tools appear in
payload cards, never in the top-level MCP tool list. Prompt and Resource use
their native MCP primitives according to who chooses the entry.

A REST adapter may publish an explicit allowlist of `(method, path, Tool ref)`
routes over the same ApplicationRuntime and Binding. It must not expose an
arbitrary-ref endpoint. GET/HEAD target only read-only Tools; writing methods
target only writing Tools. Route is a publication pointer, not a fourth node.

## Telemetry

The runtime may record `ref`, call count, error count, and last-use time for
entered Roles/Skills and invoked Tools. Merely discovering cards is not a call.
Telemetry never changes disclosure, and exporter failure must not change the
business result or exception.
