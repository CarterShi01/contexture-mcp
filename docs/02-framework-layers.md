# Design 02 — Framework Layers

> **Partly superseded by [ADR 001](adr/001-native-mcp-server.md) as of v0.0.4.**
>
> Everything below about the object model and the route/active split still
> holds. The declarative front end it described is gone: §4 has been rewritten
> to match [ADR 013](adr/013-a-constructor-is-the-declaration.md). What changed
> at v0.0.4 is the **main path**. This
> document describes rendering a declaration into per-runtime context files as
> the primary route to an agent; in v0.0.4 that became a side road, and the
> primary route is a native MCP server the runtimes connect to.
>
> **The side road was removed entirely in v0.2.0 ([ADR 005](adr/005-remove-the-target-adapters.md)).**
> `contexture.targets` no longer exists. Section 5 below is kept as a record of
> what it was, not as a description of the package.
>
> Read this for the layer boundaries and the reasoning behind them; read ADR 001
> for what replaced the target adapters at the centre, and ADR 005 for why the
> replaced half was deleted rather than kept.

## 1. Purpose

Design 01 records the object model: what a Role is, what it may reach, and how
much becomes visible at each disclosure level. This document records the shape
built around that model in `0.0.3` — how a business project states its context,
how one statement reaches several agent runtimes, and which parts of the
package are optional.

The one-sentence definition the rest of this document serves — accurate for
0.0.3, and revised in v0.0.4 as noted above:

```text
Contexture is a context framework. An application declares roles, skills,
and tools once; the framework progressively discloses them and
translates them into the skill and MCP surfaces different agent runtimes
consume.
```

The v0.0.4 revision replaces the second clause: the framework progressively
discloses them and **serves** them over MCP, rather than translating them into
what each runtime reads.

It is not an agent runtime, not a protocol, and not a file generator that
serializes one class into one file.

## 2. The problem this shape solves

Connecting one system to several agents means maintaining the same context
several times:

| Runtime | Context | Skills | MCP |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/**/SKILL.md` | `.mcp.json` |
| Codex | `AGENTS.md` | — inlined | `~/.codex/config.toml` |
| Cursor | `.cursor/rules/*.mdc` | `.cursor/rules/*.mdc` | `.cursor/mcp.json` |

Each file answers the same questions. Only the formats differ, and they drift
apart on the first independent edit. The framework's job is to make the answers
a single program and the formats a rendering concern.

## 3. Layers

```text
Business Layer            declares roles, skills and tools
        │  inherits / composes
core.model                object model, validation, the forest, the four entry points
        │  compile
        │
core.mcp_interface        what each MCP primitive carries — still no SDK
        │  bind
contexture.server         the native MCP server — the only layer importing mcp
        │  MCP
External Agent Runtime    LLM loop, planning, tool selection  (out of scope)
```

The dependency rule is one-directional and enforced by the package layout: a
layer may import the ones below it and never the reverse. Importing
`contexture.core` pulls in no transport, no adapter, and no runtime.

`0.0.2` had this layering as an idea and a flat package. `0.0.3` makes the
package structure state it, because a layering that only exists in prose stops
being true the first time somebody adds a convenient import.

### Pure managed graph foundation (graph-runtime work in progress)

`core.graph` is a separate, pure bottom layer for managed graph values,
canonicalization, references, indexes and in-memory graph algorithms. It is
not the application composition root, the existing declaration runtime in
`core.model`, or an introspection/server adapter. Its current internal imports
are exclusively within `core.graph`; its cross-layer dependency set is empty.
Adding transaction or history modules to this directory grants only same-layer
imports, not permission to import other core layers or upper layers.

`tests/test_layering.py` registers and enforces this layer alongside the
existing layers. Upper application/server imports, sibling introspection imports,
facade shortcuts, MCP SDK, network packages and filesystem I/O remain prohibited
inside the graph layer. URI checks are pure
syntax validation, not network resolution. Persistence and transport adapters
do not belong in this layer. Application, server and web may import `core.graph`
downward; the graph layer may not import any of them or any core sibling. This records
the new foundation, not a
completed managed-product release or a change to pinned parity manifests.

`contexture.application` is a real package preserving `Contexture` and
`RootFactory`, including the old dotted module name for backward imports.
Managed callers explicitly import `contexture.application.managed`; importing
declarations loads neither that module, the server, nor MCP. The owner uses
thread locks and external structural ports, not filesystem/network/process
adapters. Only the exact `server/managed_journal.py` file joins
`cli/scaffold.py` in the filesystem adapter allowlist; no server-wide exception
exists. Wheel consumer tests import both declarations and managed ownership
normally outside the checkout, without custom loaders or module registration.

The managed REST adapter in `web/managed.py` is an inbound transport consuming
structural `RouteRuntime`/`RouteRequest` ports. Its graph dependencies are pure
canonical JSON conversion, `GraphError`, and `RoutePublication` values; it does
not construct an owner, open a journal, or import an application, server, or MCP
SDK. Allowing `web` to depend downward on `core.graph` expresses this boundary
without duplicating graph contracts in a new port layer. Runtime ownership and
Host composition remain outside web, and the graph layer remains I/O-free.

Managed REST's query decoding permits only the exact
`from urllib.parse import parse_qs` import in `web/managed.py`, not a network
adapter exemption. The existing exact `web/surface.py` and `debug.py` transport
exceptions are unchanged. Negative architectural probes reject other network
imports in managed REST, parser imports in adjacent modules, filesystem calls,
application/server/SDK dependencies, and reverse graph-to-web imports. No
web-wide network permission or graph I/O permission is granted.

### 3.1 Why `role_runtime` was renamed

The package was called `role_runtime` while the runtime was the whole product.
Under this shape the runtime is the optional layer and the model is the
product, so the name pointed at the least essential part. It is now
`contexture`, matching both the project and the definition above.

## 4. Declaration

### 4.1 One door, and a constructor is the whole of it

A node is a class whose `__init__` hands its identity to the base and builds
whatever the node holds:

```python
class K8sOperator(Role):
    def __init__(self) -> None:
        super().__init__(
            name="k8s-operator",
            description="Operate and diagnose Kubernetes workloads.",
            instructions="Inspect before changing the cluster.",
            skills=[DiagnoseDeployment()],
            tools=[GetPods(), RolloutRunbook()],
        )
```

The base constructor is also the imperative door — `Role(name=..., ...)` is
exactly what the line above calls — so a graph assembled at run time and a
graph declared as classes are the same objects, and every consumer accepts
either without knowing which was used.

Through v0.4 there was a second door: a class *body* whose attributes were
read by `core.model.declarative`. ADR 013 removed it. What it bought was three
lines of syntax; what it cost was a metaprogramming layer, a shadow object
graph built at import, and two inferences no other language can reproduce.

### 4.2 Why this does not contradict "composition over inheritance"

Design 01 §5 argues that a team is modeled by composition, not inheritance.
That still holds. The two mechanisms answer different questions:

```text
class K8sOperator(Role)      what this node IS      — a kind of context node
children=[Troubleshooter()]  what this node HOLDS   — the team structure
```

Subclassing never expresses containment. A role that coordinates other roles
builds them in `children`, and its own constructor is where that happens.

Inheritance still earns its place twice. A `Tool` subclass overrides `invoke`,
which is behaviour. A `Role` or `Skill` subclass overrides nothing — it exists
to be a **named unit with a constructor**, so that twenty procedures are twenty
things a project can name, reuse, and give a common base whose constructor
supplies the fields they share.

### 4.3 Nothing is inferred

| Once derived from | Now |
|---|---|
| class name → node name | stated in the constructor |
| docstring → routing description | stated in the constructor |
| class attributes → member lists | built in the constructor |

The reason is portability rather than taste. This object model is meant to
exist in TypeScript and Go as well, and neither can reproduce those
derivations: a bundler renames classes, and no Go or TypeScript runtime can
read a doc comment. A field that is optional in one implementation and required
in the others is one declaration meaning two things.

The one thing still read off code is a tool's input schema, derived from
`invoke`'s type hints. That is reflection over *what the code already is*
rather than a guess at what its author meant — and even there, what the
cross-language spec pins is the JSON Schema that reaches the wire, never how it
was derived. Go reflects an argument struct; TypeScript states a schema object.

### 4.4 Nothing exists until it is registered

A class is a zero-argument factory. Its members are built by its own
constructor, so importing a module full of declarations constructs **no nodes
at all**, and a `ControllerManager` calling one factory is the single moment a
node comes into existence.

That is the moment it can be told where it hangs (`path`) and handed what it
may reach outside the process (`channels`), which is why registration and
construction are deliberately the same event.

The cost is that declaration errors surface at registration rather than at
import. Registration is `main()`'s first act, so this is still start-up rather
than run time — and it is the only moment the other two implementations can
share, since Go has no import-time hook at all.

### 4.5 One implementation constraint worth recording

`@dataclass(slots=True)` **rebuilds the class object** rather than decorating it
in place, so a zero-argument `super()` inside a class-creation hook raises
`TypeError`: the method's `__class__` cell still points at the discarded
original. `Prompt` and `Resource` once carried such a hook and no longer do; if
one is ever added back, it must name its class explicitly.

The sibling constraint — that `getattr(cls, "name")` returns a slot descriptor
rather than `None` for an undeclared scalar — mattered only while class bodies
were read. Nothing reads them now.

## 5. Targets *(removed in v0.2.0)*

> This section describes `contexture.targets`, which was deleted in v0.2.0. It
> is kept because the capability matrix in 5.2 is still the clearest statement
> of *what each runtime cannot express* — the fact that motivated serving the
> declaration instead of rendering it. See
> [ADR 005](adr/005-remove-the-target-adapters.md).

### 5.1 The adapter contract

A `TargetAdapter` renders a declaration as files:

```python
class TargetAdapter(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[TargetCapabilities]

    def render(self, role, *, registry=None) -> ArtifactSet: ...
```

Two rules hold for every adapter.

**Nothing is written.** `render` returns paths and bytes. Installing them is a
separate call in `contexture.targets.writer`, which is the only module in the
package that touches a filesystem. That keeps adapters testable without a
temporary directory and makes a dry-run preview possible.

**Losses are reported.** Each adapter declares a `TargetCapabilities`, and the
base class turns the gap between those capabilities and the declaration into
notes. An adapter states its limits once as data instead of remembering to
write the same warnings by hand.

### 5.2 The capability matrix

| | Claude Code | Codex | Cursor |
|---|---|---|---|
| separate skill files | yes | no | yes |
| MCP configuration | yes | yes (user-level) | yes |
| per-role tool allowlist | no | no | no |
| progressive disclosure | yes | no | yes |
| nested roles | yes | no | no |

Every "no" becomes a note on the rendered set. The most consequential is the
allowlist row: no target can express a per-role tool grant, so on all three the
grant is enforced by the host and not by the generated file. Saying that out
loud is the point — a surface that looked authoritative while silently
widening a grant would be a security problem, not a formatting one.

### 5.3 Determinism

`Artifact.digest` and `ArtifactSet.digest` are content hashes over sorted
paths and bodies. Recompiling and comparing digests is how a caller detects
that an installed surface has drifted from the declaration. The writer uses the
same comparison per file so unchanged files are left alone and only real
changes touch timestamps or wake file watchers.

## 6. The inbound boundary

Through `0.0.3` this section described a port — `MCPHostPort` — that an
application would implement to serve its declarations, alongside an `MCPClient`
for calling somebody else's server. Both are gone.

`contexture.server` is the inbound boundary now, and it is not a port but a
server: a `ControllerManager` is compiled into an `Index`, `ContextureServer`
builds a `Surface` of three doors over it and installs that onto the official
SDK, and runs. It is the only layer that imports `mcp`, which is what
keeps the object model describable without a wire protocol in the room. ADR 001
records why the arrow turned around; ADR 003 records why the outbound half was
removed rather than kept as an option.

## 7. Where a tool call happens

A tool call is not a kernel object. The model *describes* a tool and knows how
to execute one; deciding whether to call it belongs to an agent runtime, and
carrying the call over the wire belongs to the SDK:

```text
core           describes a Tool, and owns its `invoke`
server         derives the schema, projects it, dispatches the call
agent runtime  decides whether to call at all          (out of scope)
```

There is no `ToolCall` or `ToolResult` type in this package. The SDK owns the
wire form, and inventing a parallel one would be a second protocol wearing the
first one's clothes.

## 8. Invariants added in 0.0.3

### Layering

1. `contexture.core` imports no layer above it.
2. Only `contexture.cli` performs filesystem I/O — and it does so only to
   scaffold a project. (In 0.0.3 this named `contexture.targets.writer` as
   well; that module is gone.)
3. No layer below `contexture.server` performs network I/O.
4. *(added in v0.2.0)* No layer below `contexture.server.contract` writes prose
   an agent reads. A failure carries facts; the sentence is composed where the
   gateway vocabulary is. See
   [ADR 006](adr/006-errors-carry-facts-and-the-contract-is-one-module.md).

### Declaration

- A subclass is a class whose constructor calls the base constructor. There is
  no class-body reading, no `__init_subclass__`, and no inference.
- Importing a declaration constructs no nodes. `ControllerManager` is the only
  place a node comes into existence, and registration is where it is told its
  address and handed its channels.
- Members are listed by kind — `children`, `skills`, `tools` — because which of
  the three a capability belongs in is the modelling decision the framework
  asks for, and because three typed collections are what Go and TypeScript can
  express where one mixed list is neither.

### Targets

1. `render` never writes.
2. Every capability a target cannot express produces a note.
3. Identical declarations render byte-identical artifacts.
4. An artifact path is relative and stays inside the project tree.
5. A role's rendered surface lists only that role's own tools.

## 9. Deliberate non-goals, restated

Unchanged from Design 01 §22, and reinforced here: no agent loop, no planner,
no model client, no orchestration, no new protocol. `0.0.3` added a declaration
front end and several rendering back ends around the same kernel; `0.0.4` turned
the main path into a server and removed the client half entirely (ADR 001, ADR
003). Neither moved the kernel toward being an agent runtime.
