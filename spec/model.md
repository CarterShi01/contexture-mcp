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
| Role | name, description, instructions, members, optional uses refs, optional pre-process and post-process members | A stable responsibility and containment boundary an agent can enter. |
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
instructions, Tool execution facets or schemas, process-member designations or
contracts, content, invocation results, or recursive expansion.
Batch inspection MUST preserve request order and declaration order, MUST be
atomic, and MUST enforce the same selection and model-visibility boundary as
open. Breadth is multiple requested refs; depth is a later explicit inspection
of refs returned as cards.

A Role exposes complete immediate sibling groups when opened. Reverse
dependencies and whole-graph facts are available only through an explicitly
declared introspection Tool; ordinary disclosure does not reveal unentered
branches.

### Optional Role process members (0.16 extension)

This is the normative process-member extension accepted for Python 0.16.0 in
[ADR 023](../docs/adr/023-process-members-and-instruction-emphasis.md), which
supersedes the 0.14 Publication extension. It does not establish implementation
or cross-language parity; the legacy fixture/golden inventories remain pinned
to 0.12.

`PreProcess` and `PostProcess` MUST specialize Role, not add node kinds. Each
keeps `kind="role"`, `group="roles"`, and the Role constructor. Python exports
both and removes the framework-level `Publication` name without an alias.

A Role MAY designate one constructed `PreProcess` as `pre_process` and one
constructed `PostProcess` as `post_process`. A class, an ordinary Role, or the
opposite process kind MUST be refused. Absence MUST preserve exact existing
ROUTE and ACTIVE output, with no designation, added instruction, enabled flag,
no-op member, or implicit construction for explicit `None`.

Containment traversal MUST order the optional pre-process first, then child
Roles, then the optional post-process, Skills, and Tools. Both process members
receive ordinary uniqueness, sharing, cycle, ref, path, Channels, Binding, and
complete-subtree behavior. Neither joins `branches()` or the alternative-work
roster. Containment MUST NOT implicitly inherit a process member. Explicit
nesting is ordinary acyclic containment and adds no semantics of its own.

Business-authored `Role.instructions` MUST remain unchanged. ACTIVE compilation
MUST compose the pre-process framework contract before that text and the
post-process framework contract after it. Each designated member MUST appear as
an ordinary routing card in `roles`; `pre_process` and `post_process` MUST be
STRING fields equal to the corresponding card's actual view-supplied ref. The
member's own instructions and capabilities MUST remain hidden until opened.

Every process contract MUST use one fixed framework head and tail, identify its
kind inside the block, and lift the required open call onto a `>>> REQUIRED:`
line. It MUST use the existing `contexture_open` gateway and the actual ref.
The pre-process contract MUST require opening before owner work, distinguish
opening from execution, and direct the agent to complete the preparation and
then return to the owner's instructions; blockers and failures MUST be reported
rather than ignored. The post-process contract MUST require opening before
finishing, distinguish opening from execution or success, preserve approval
boundaries, and require truthful blocker, failure, and pending-approval reports.
ROUTE and INSPECT MUST expose neither designations nor contracts.

Complete-subtree selection includes both process subtrees. Selecting a process
member alone MUST expose only its subtree. `uses` MUST NOT widen selection, and
Prompt-only owners and process members MUST remain hidden from ordinary model
disclosure. If a view cannot supply either designated member's card, composition
MUST refuse without revealing its ref or emitting a dangling contract.

Python also exports `binding_instruction(source, body, *, action=None)` for an
application to mark its own hard rules with the same stable shape under its own
authority. It MUST reject empty sources and sources beginning with
`contexture`, case-insensitively. The internal framework composer MUST NOT be
part of the public authoring API.

A process member is a procedure an agent is instructed to perform, not an
invariant the framework guarantees. Enforce a guarantee in the Tool that would
otherwise violate it. Process members introduce no automatic execution, start
or finish event, hook, `Role.invoke`, host-specific runtime, or guarantee of
external Agent compliance. The five runtime gateways, Tool-only effects, and
existing approval and authorization boundaries remain unchanged.

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

Prompt and Resource are exposure publications, not process Roles or nodes.
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
