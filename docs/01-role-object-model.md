# Design 01 — Progressive Role Object Model

> Scope note: this document covers the **object model** — what a Role is, what
> it may reach, and how much becomes visible at each disclosure level. The
> framework built around it (class-syntax declaration, target adapters, the
> layer boundaries, the server) is [Design 02](02-framework-layers.md).

> **Status note (v0.4.0).** Written when the object model had four kinds. It
> now has three: `Resource` was deleted, and content is a read-only Tool that
> takes no arguments — see
> [ADR 009](adr/009-the-protocol-plane-is-not-the-object-model.md). Read every
> mention of a `Resource` node below as a record of what the model used to
> hold. Everything this document says about Role, Skill, Tool, the two compile
> levels and the boundaries between them is current, and ADR 009 makes it more
> so: with the fourth kind gone, "two levels and no third" has no exception
> left. The directory names it uses predate
> [ADR 010](adr/010-the-directories-are-the-architecture.md).

## 1. Purpose

This document records the design reasoning behind the object model at the
center of Contexture. It begins with the members introduced during the
incremental design sequence and then shows how those members fit into the
terminal architecture.

The design targets Python and uses the Model Context Protocol revision
`2026-07-28`, whose wire messages use JSON-RPC 2.0.

The most important architectural separation is:

```text
MCP defines how an agent runtime reaches a capability provider.
This project defines how a provider organizes its capabilities into Roles.
```

`Role`, `Skill`, `ContextNode`, `ref`, and the compile levels are model-layer
concepts. They are not additions to the MCP wire protocol, and none of them
appears on it: the protocol surface stays flat, and the tree travels inside the
payload of two framework tools.

Tools and Resources are the two capability kinds MCP defines, and this model
deliberately has no third kind beside them — see §4.1.

## 2. Design goals

The model should satisfy six goals.

1. A Role must be an ordinary Python object.
2. A Role may contain child Roles through object composition.
3. A Role may reuse Skills without copying their implementation knowledge.
4. A Role owns the tools and resources it declares, and no others.
5. The Host must progressively expose context instead of loading the complete
   team, every Skill, and every Tool schema at once.
6. One compile interface should work across different context-node types.

The model should remain understandable before adding registries, transports,
policy engines, persistence, or distributed execution.

## 3. Protocol baseline

The formal MCP `2026-07-28` revision is stateless at the protocol core. Every
request is self-contained and carries its protocol version and client
capabilities. MCP messages use JSON-RPC 2.0.

For Tools, the protocol defines concepts such as:

- `tools/list`
- `tools/call`
- Tool `name`
- optional Tool `description`
- required Tool `inputSchema`
- optional Tool `outputSchema`
- optional Tool annotations

The Role model does not redefine these fields. A `Tool` states the operation in
Python and lets the server layer derive the protocol shape from it, so the wire
form has exactly one author.

## 4. The vocabulary

| Type | Question answered | Primary responsibility |
|---|---|---|
| `Role` | Who owns this responsibility? | Coordinate a bounded area of work. |
| `Skill` | How should this class of work be performed? | Supply reusable workflow knowledge. |
| `Tool` | Which operation can this Role run? | Execute one typed Python method. |
| `Resource` | Which content can this Role read? | Address content without loading it. |
| `Disclosure` | Where is the agent, what is next, and what has it paid for? | Deliver the role skeleton whole, resolve a reference, and open one node. |

A useful shorthand is:

```text
Role       = who
Skill      = how
Tool       = executable operation
Resource   = readable context
Compiler   = context disclosure
Discovery  = navigation over the declared graph
```

### 4.1 Why there is no separate Data type

An earlier revision of this model carried its own `DataSource`, `DataBinding`,
and `DataProvider` types beside the MCP ones. That produced two parallel
least-privilege systems answering the same question — which external thing may
this Role touch — with two grant formats, two runtime paths, and two places to
audit.

MCP already defines Resources for exactly this purpose. The host-layer Data
types were therefore removed and every form of contextual information —
documents, configuration, knowledge, runbooks — is now declared as a
`Resource`, whose `read()` produces the content when something asks for it.

A data source with an awkward shape is wrapped by a `Resource` subclass, not by
reintroducing a second abstraction beside it.

## 5. Step 1: Role as a recursive composite

The first model required only three members:

```python
@dataclass
class Role:
    name: str
    description: str
    children: list["Role"]
```

`children` is the key object-oriented decision. A child Role is a member object,
not necessarily a Python subclass.

```text
engineering-team
├── k8s-troubleshooter
├── k8s-operator
└── github-liaison
```

This is composition. It models an operational team structure directly and
allows the same Role type to represent every level of the tree.

Python inheritance would answer a different question:

```text
Is FinancialResearcher a specialized kind of Researcher?
```

Composition answers the team question:

```text
Does ResearchLead contain and coordinate Researcher Roles?
```

The team model primarily needs composition.

## 6. Step 2: routing description versus active instructions

A single prompt field is insufficient because routing and execution have
opposite context requirements.

- `description` should be small and broadly visible.
- `instructions` may be detailed and should become visible only after the Role
  is selected.

```text
description  = when should the Host or model select this Role?
instructions = how should the selected Role behave, and how should it use
               what it holds?
```

This distinction is the foundation of progressive disclosure.

The second half of that question is what makes a composite Role work. A Role
that coordinates others does nothing itself, so every word of its instructions
is about its members: which branch a task belongs to, what order they run in,
and what must be established before one of them is opened. `KubernetesPlatform`
in `examples/incident/` holds two child Roles and no tools, and its
instructions are entirely orchestration — *route to the specialism the task
belongs to, and open only that one; diagnose before remediating*.

Nothing else can carry that text. Opening a Role returns its instructions
alongside a route card for every member, and a card is three short strings —
it says what a member is, never when to reach for it relative to its siblings.
An agent that is handed cards without that sentence is left to infer the order
from the names.

## 7. Step 3: Skill as reusable workflow knowledge

A Role owns a responsibility, but a Skill contains a reusable method.

For example:

```text
Role:  k8s-troubleshooter
Skill: inspect-pod-failure
```

The Skill can explain a sequence such as:

1. Inspect Pod status.
2. Read current logs.
3. Read previous logs after a restart.
4. Inspect Events.
5. Correlate the evidence.

The Skill does not need to own the external functions that perform those steps.
It can guide the model to use several MCP Tools, possibly from several Servers.

This separation prevents workflow knowledge from being copied into every Role
or hard-coded into Tool implementations.

## 8. Step 4: ContextNode and the uniform lifecycle

Once both Role and Skill needed the same route-versus-active lifecycle, the
model introduced `ContextNode`.

```python
class ContextNode(ABC):
    name: str
    description: str

    def compile(self, level): ...
    def _compile_route(self): ...
    @abstractmethod
    def _compile_active(self): ...
```

The base class deliberately does not own every field that happens to appear in
current subclasses.

For example:

- Role active context contains `instructions` plus child routes.
- Skill active context contains `instructions`.
- MCP Tool active context contains `inputSchema`, not instructions.
- MCP Resource active context contains a URI and media type, never content.

The stable abstraction is the lifecycle, not a forced universal payload.

This supports polymorphism:

```python
node.compile("route")
node.compile("active")
```

The caller does not need a growing `isinstance` chain for Role, Skill, Tool, and
Resource.

## 9. The two compile levels

### 9.1 Route level

The route level is intentionally small:

```json
{
  "kind": "mcp_tool",
  "name": "get_pod_logs",
  "description": "Read logs from a Kubernetes Pod container."
}
```

Its purpose is selection, not execution.

### 9.2 Active level

The active level reveals type-specific details. An active MCP Tool exposes its
schema:

```json
{
  "kind": "mcp_tool",
  "name": "get_pod_logs",
  "description": "Read logs from a Kubernetes Pod container.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "namespace": { "type": "string" },
      "pod": { "type": "string" }
    }
  }
}
```

An active Role does not recursively activate all descendants. It exposes:

- its own instructions;
- direct child Role route cards;
- Skill route cards;
- granted MCP Tool route cards;
- granted MCP Resource route cards.

This invariant prevents an accidental full-tree context expansion.

## 10. Why `kind` belongs in the route card

Before Tools existed, a route card containing only `name` and `description` was
sufficient. Once a Role could expose child Roles, Skills, Tools, and Resources,
the Host needed to know how the selected item should be activated.

```json
{
  "kind": "skill",
  "name": "inspect-pod-failure",
  "description": "Diagnose a failing Pod."
}
```

`kind` is a Host routing field. It is not an MCP Tool field added to the wire
protocol.

## 11. Step 5: Tool as a ContextNode

A `Tool` is a capability this application owns and can execute. It is stated as
a typed Python method:

```python
class GetPodLogs(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="get_pod_logs",
            description="Return recent container logs for one Pod.",
            read_only=True,
        )

    async def invoke(self, namespace: str, pod: str) -> str:
        ...
```

The constructor hands this tool's identity to the base, and `invoke` is the
behaviour — the one method a business writes. Nothing is inferred from the
class name or the docstring; see ADR 013.

Nothing here writes a JSON Schema. The schema published in a tool's card is
derived from `invoke`'s type hints when the graph is projected onto a server,
which is why this layer never has to know what JSON Schema looks like.

A Tool's route card carries its name and description. Its active surface adds
the parameter names and the `read_only` classification — not the full schema,
because a connected agent already received that from the protocol, and
repeating it inside a disclosure payload would spend context on something the
host has handed over anyway.

```text
Tool describes and performs one operation.
The server layer derives its schema and carries the call.
```

## 11.1 Content as a Tool, Resource as a publication

Content the application already holds is an argument-free, read-only Tool:

```python
class CrashLoopRunbook(Tool):
    def __init__(self) -> None:
        super().__init__(name="crash_loop_runbook", description="...", read_only=True)

    async def invoke(self) -> str: ...


class CrashLoopRunbookDocument(Resource):
    def __init__(self) -> None:
        super().__init__(
            opens="incident-response/crash_loop_runbook",
            uri="contexture://runbooks/crash-loop-backoff",
            mime_type="text/markdown",
        )
```

The Tool is the node a model discovers and invokes. `Resource` is not a
ContextNode: it publishes that same Tool to MCP's host-controlled Resource
primitive under a stable URI. It creates a second address, not a second copy of
the content or business logic.

```text
Opening a Role yields the Tool card, not the bytes.
Invoking the Tool or reading its published URI yields the same content.
```

This is the whole reason a resource is a resource rather than a paragraph
pasted into a Skill. Discovering a hundred runbooks costs a hundred
descriptions, not a hundred documents.

Publication requires an argument-free read-only Tool. The MCP Resource itself
has no write operation, while model-controlled access still travels through
`contexture_invoke_read_only`.

## 12. Why there is no provider object

Through `0.0.3` the model carried `MCPServer`, `MCPBinding`, and a pair of
descriptor types for somebody else's catalog. That shape belonged to a host —
the thing that connects *out* to providers on an agent's behalf.

Contexture is the provider. It has no catalog but its own, so there is nothing
for a provider object to point at and nothing for a binding object to subset.
ADR 003 records the removal; the argument it rests on is that a role declares
what it owns, and ownership needs no grant.

```text
A Role declares a Tool.  That is the whole relationship.
```

## 13. Refs are the agent's position

The role tree is not the protocol surface. MCP tool and resource lists are flat
and, since the `2026-07-28` revision, explicitly stateless: a server may not
vary them per connection or as a side effect of an earlier call.

So the graph travels inside the payload of two framework tools, and an agent's
position in it is a **ref** the agent carries:

```text
role:engineering-team/k8s-troubleshooter
skill:engineering-team/k8s-troubleshooter#inspect-pod-failure
tool:engineering-team/k8s-troubleshooter#get_pod_logs
resource:engineering-team/k8s-troubleshooter#contexture://runbooks/crash-loop
```

The leaf is separated by `#` rather than `/` because resource URIs contain
slashes of their own, and an address a reader cannot split by eye is an address
that will eventually be split wrong.

Because the ref is the position, the server keeps none. Opening a ref is a pure
function of its ref, never of what was asked before it — which is what makes
traversal legal on a stateless surface, and what lets one server carry a whole
forest of roots instead of one process per leaf role.

## 14. Disclosure is not authorization

On a flat surface, per-role authorization is not achievable. A tool name that
exists can be called by anyone who can see the list; nothing stops an agent from
skipping discovery and calling a tool directly, and nothing here pretends to.

What disclosure controls is **knowledge**. A Skill's procedure, its ordering,
and its constraints live behind `contexture_open`. An agent that skips
ahead can run a tool; it cannot know what the runbook says about exit code 137,
or that restarting first repairs nothing.

**Authorization stays with the host**, which the specification already makes
responsible for keeping a human in the loop. The model informs that decision by
projecting `read_only`, and by never letting that classification become an
argument.

```text
Hidden is not the same as forbidden.
Disclosure shapes what an agent knows; the host decides what it may do.
```

## 15. Why `read_only` is a classification and not an argument

`read_only` is stated once, in the class body, as a `ClassVar`:

```python
class DeletePod(Tool):
    read_only = False
```

It is projected onto the protocol's `readOnlyHint` annotation, where a host can
act on it, and it never appears in the tool's input schema. The reason is
direct: a model that could pass its own approval flag would be approving its own
writes, which is not approval at all.

The same rule generalizes. Anything the framework needs to state about a call —
as opposed to anything the model needs to supply — must reach the tool by a path
the model cannot fill in.

```text
The model fills business arguments.
The framework fills framework arguments.
They are never on the same plane.
```

## 16. Current object graph

```mermaid
classDiagram
    class ContextNode {
        +name: str
        +description: str
        +compile(level)
    }

    class Role {
        +instructions: str
        +children: Role[]
        +skills: Skill[]
        +tools: Tool[]
        +resources: Resource[]
    }

    class Skill {
        +instructions: str
    }

    class Tool {
        +read_only: bool
        +invoke(...)
        +parameters()
    }

    class Resource {
        +uri: str
        +mime_type: str
        +read()
    }

    ContextNode <|-- Role
    ContextNode <|-- Skill
    ContextNode <|-- Tool
    Role *-- Role : children
    Role *-- Skill : skills
    Role *-- Tool : tools
```

Three node types, one base class, and a role that holds only what it declares.
Prompt and Resource are protocol publications outside this graph.

## 17. Terminal-state additions

The project includes the natural next layers so the current model does not need
to be redesigned later.

### 17.1 Content is declared once and may be published twice

A Role holds an argument-free read-only content Tool. A model reaches it from
the Role's ordinary Tool card. An optional Resource declaration publishes the
same ref to hosts at a URI. The tree never owns a second Resource node.

### 17.2 Disclosure

One class holds the navigation model, with three methods:

```python
tree.skeleton()          # every role in the forest, as cards
tree.find(ref)           # a reference to the node it addresses
tree.open(ref)           # that node's detail, plus a card for each member
```

**Withdrawing the previous §17.2.** Earlier revisions kept a separate
`RoleCompiler`, on the argument that a `ContextNode` knows how to compile
*itself* while something else must decide **which** node and **at which level**,
and that a `CompileRequest` is where that decision is written down.

The argument was sound and the object was not what carried it. Of the places
that decided which node and at which level, one went through the compiler and
the rest called `node.compile()` directly; the one that did passed an empty
selection and discarded the wrapper it got back. Nothing in the package ever
constructed a non-empty `CapabilitySelection`, so the request that needed
somewhere to live did not exist.

The decision is real, and it is written down — as the reference. A ref's shape
says which node, and which of `discover`, `open` and `read` was called says at
which level. `Disclosure` is where that reading happens; the facts it reads —
every address, and the checks a cycle or a duplicate name needs the whole forest
to see — belong to the `Index` it is compiled over (ADR 016).

### 17.3 The server projection

`contexture.server` is the only layer that imports the official SDK. It
translates, and holds no business rules of its own:

```text
Role tree                        MCP
------------------------------   -------------------------------------
Disclosure.skeleton             the tool `contexture_discover`
Disclosure.open                 the tool `contexture_open`
Tool.invoke, read-only           the tool `contexture_invoke_read_only`
Tool.invoke, writing             the tool `contexture_invoke`
Resource publication             MCP's native resource primitive
the skeleton                     server instructions
```

Nothing else reaches the surface. A registered capability is one every session
pays for whatever the user asked, and the protocol forbids varying that list per
connection (§13), so a capability becomes deferrable only by not being there.
Its name, description and schema travel inside `contexture_open`'s payload
instead, derived from `invoke` by `Tool.from_function`, which needs no server.

Two invariants are load-bearing enough to restate. **The tree never becomes the
surface** (§13). And **`read_only` never becomes an argument** (§15) — with
capabilities off the wire it becomes *which door*, and each door carries the
matching `readOnlyHint`. A call whose door disagrees with its ref is refused
rather than executed, because the host made its approval decision from that
door.

## 18. Complete progressive flow

A typical interaction follows this sequence. Every step is one MCP call, and the
agent's position between steps is the ref it carries, not state the server keeps.

```text
0. The session opens.
   - Four fixed tools, and a bounded breadth-first Role roster in instructions.
   - No procedure, no schema, no content.

1. contexture_discover()
   - The model-visible roots, one card each. Descendants arrive one level at a
     time after a Role is opened.

2. contexture_open("kubernetes-platform/incident-response")
   - That role's instructions, and a card for each child Role, Skill, and Tool
     it holds — Tools with the schema needed to call them.
   - Sibling specialisms are not opened, and cost nothing.

3. contexture_open(".../diagnose-crash-loop-backoff")
   - The skill's complete procedure enters context, here and nowhere else.

4. contexture_invoke_read_only(ref=".../get_pod_status", arguments={...})
   - Arguments are validated against the schema step 2 delivered.
   - The host decided whether to ask a human from the door's readOnlyHint.

5. contexture_invoke_read_only(ref=".../crash_loop_runbook")
   - The content Tool runs now, and only now. Its card cost a line; the document
     costs the document. A host may independently read the same content at its
     published Resource URI.

6. The result returns to the agent runtime, which decides what to do next.
   That decision is out of scope here.
```

Steps 1–3 are disclosure and cost context in proportion to what was chosen.
Steps 4–5 are execution and are reachable without steps 1–3 — see §14.

## 19. Core invariants

The implementation protects these invariants.

### Progressive-disclosure invariants

1. `route` never contains type-specific detailed execution content.
2. An active Role does not recursively activate descendants.
3. A selected capability is activated explicitly by the compiler.
4. Compiling a Resource yields a descriptor; only the Runtime yields content.

### Object-model invariants

1. Direct child Role names are unique within one Role.
2. Skill names are unique within one Role.
3. One Role has at most one Binding for a given Server id.
4. Role composition cannot contain cycles.

### MCP invariants

1. Tool names are unique across the whole served graph, because the protocol
   surface is flat.
2. Resource URIs are unique across the whole served graph, for the same reason.
3. A tool's input schema is derived from `invoke`, never hand-written.
4. `read_only` is projected onto `readOnlyHint` and never onto the input schema.
5. Opening a ref is a pure function of that ref.
6. The served tool and resource lists do not vary per connection.

### Security invariants

1. Hidden is not the same as forbidden.
2. Disclosure controls knowledge; the host controls authorization.
3. A framework classification never becomes an argument a model can fill in.
4. `contexture.core` cannot import the SDK, enforced statically and at runtime.

## 20. Why direct objects are retained

The current model stores direct Python objects:

```python
Role(children=[child_role], tools=[tool], resources=[resource])
```

This is intentional because the goal is to make the object relationships easy
to understand.

A persisted or distributed system will likely split declaration and runtime:

```text
RoleSpec with stable references
        ↓ resolve/link
Role objects with live implementations attached
```

The Registry and the ref grammar already establish the boundaries needed for
that split. The core Role API does not need to change when serialization is
introduced.

## 21. Extension points

The terminal structure supports the following additions without changing the
core object hierarchy:

- persisted `RoleSpec` documents and versioned references;
- Skill packages loaded from local directories or registries;
- MCP Prompts and resource subscriptions;
- a per-call context object carrying progress, logging, and elicitation
  (ADR 002);
- token-budget-aware context compilation;
- disclosure telemetry: which refs are opened, and which tools are called
  without their skill ever being read;
- observability, audit events, and deterministic fingerprints.

Each belongs behind an existing boundary rather than inside the base Role class.

## 22. Deliberate non-goals

Contexture does not attempt to be a full MCP SDK or a complete multi-agent
orchestrator. In particular, it does not automatically implement every optional
MCP feature, LLM provider adapter, persistence backend, distributed scheduler,
or authorization system.

Its purpose is narrower and architectural:

```text
Provide a coherent, runnable object model that can grow from a simple Role tree
into a secure progressive Agent Host without invalidating the early concepts.
```

## 23. Code map

| Design concept | Source file |
|---|---|
| Disclosure lifecycle, shared by every node | `contexture/core/model/node.py` |
| Role | `contexture/core/model/role.py` |
| Skill | `contexture/core/model/skill.py` |
| Tool | `contexture/core/model/tool.py` |
| Registration: where a node comes into existence | `contexture/core/model/manager.py` |
| The tree: skeleton, resolution, opening | `contexture/core/model/tree.py` |
| What each MCP primitive carries | `contexture/core/mcp_interface/` |
| The MCP server | `contexture/server/` |
| Complete example | `contexture/demo/` |

## 24. Official references

- MCP specification: https://modelcontextprotocol.io/specification/2026-07-28
- Architecture: https://modelcontextprotocol.io/specification/2026-07-28/architecture
- Tools: https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- Streamable HTTP: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- Schema reference: https://modelcontextprotocol.io/specification/2026-07-28/schema
