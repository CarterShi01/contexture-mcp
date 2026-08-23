# Contexture

**One Controller framework for agent and human Views.**

MCP lets an agent *call* your system. REST lets a human-facing application do
the same. Contexture keeps both on one Controller model.

The official SDK turns a Python function into a tool. Contexture turns a domain
into a capability graph an agent can navigate — one with responsibility
boundaries, procedural knowledge, and a context bill you pay only for what gets
selected.

Declare your roles, skills and tools once. Contexture serves them through two
explicit Host adapters: a progressively disclosed native MCP surface for
Claude Code, Codex and other agents, and a REST/ASGI surface for human-facing
dashboards. Both resolve and execute the same compiled Tool bindings.

It does not run an agent loop, choose tools, or talk to a model. It is what
those runtimes connect to.

## Two models

Everything in this repository follows from two sentences.

**The runtime model — what Contexture *is* while it runs.**

> Contexture organizes Role, Skill and Tool into one compiled Controller
> runtime. Its MCP Host discloses that tree progressively to agents; its REST
> Host publishes an explicit route allowlist to human-facing applications.

**The framework model — how a developer *uses* it.**

> Contexture provides the abstractions, lifecycle, inversion of control and
> shared execution runtime. A business developer **subclasses** three node
> kinds to define capabilities, then selects MCP and/or explicit REST Host
> adapters as Views over the same Controller tree.

The first answers *what is running*. The second answers *what do I write*.

They meet at the compiled runtime exposed by `contexture.server`. The MCP Host
adapter lives there because it is the only layer permitted to import the MCP
SDK; `contexture.web` is the independent REST/ASGI Host adapter, and
`contexture.core` remains forbidden from either transport.

The two are also the admission test for every new concept. Before a compiler, a
registry, a resolver, a descriptor, or a request object earns a place here, it
answers one question:

> Does this help a business developer **define** a capability, or help the
> framework **run** one?

A concept that does neither is a spare part, however well made.

## The programming model: Object-Oriented Programming

The programming model is **Object-Oriented Programming (OOP)**. Not a decorator
registry, not a DSL, and no metaprogramming that reads your class body and
guesses what you meant. The framework ships an object model; the business layer
extends it by subclassing, and each subclass says what it is in its own
constructor:

```python
class GetPodLogs(Tool):            # a capability you own
class DiagnoseCrashLoop(Skill):    # a procedure you own
class IncidentResponder(Role):     # the boundary that holds them
```

One lazy `Contexture` application names the roots once. Everything beneath one
is reached by traversal, so a new tool is a line in the constructor of the
role that holds it and nothing else.

### Three planes, one verb

There are three planes, because MCP splits its primitives by who decides when
one is used. What differs between them is what they *carry*, not how you write
them: all five are classes, and all five hand their identity to a base class.

| What you are doing | Mechanism | You write | Fails at |
| --- | --- | --- | --- |
| Define a capability | subclass `Tool`, implement `invoke` | a class | registration |
| Define procedural knowledge | subclass `Skill`, state `instructions` | a class | registration |
| Define a responsibility boundary | subclass `Role`, build its members | a class | registration |
| Define content | subclass `Tool`, `read_only`, no arguments | a class | registration |
| Open a door for a **person** | subclass `Prompt`, state `opens` | a class | server start |
| Publish an address for a **host** | subclass `Resource`, state `opens` and `uri` | a class | server start |
| Decide what the server exposes | nothing — the gateway is four fixed tools | — | — |

**Every one of them is a class whose constructor hands its identity to the
base.** One way to write a declaration, on every plane:

```python
class GetPodLogs(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="pod_logs",
            description="Return recent container logs for one Pod.",
            read_only=True,
        )

    async def invoke(self, namespace: str, pod: str) -> str:
        ...
```

There are exactly **three** node kinds and there is no fourth. Content is not
one of them: it is a read-only `Tool` taking no arguments, and the file it sits
in is organisation rather than a kind. `Prompt` and `Resource` are not nodes at
all — each holds a reference *string* naming a node the tree already owns,
which is what keeps one capability from becoming two declarations that can
disagree. They are written the same way; the type keeps the difference.

### One application declaration

An application is the one composition root a project writes:

```python
app = Contexture(name="my-context", roots=(MyContextAssistant,))
```

It may later name Channels, Prompts, and Resources as well. Importing `app`
does not build a graph or open a connection. `contexture serve` and a custom
`main()` compile the same declaration when they run.

Four OOP mechanisms carry weight here, and each does a job the alternatives
cannot.

**Inheritance is the extension point, and only where there is behaviour to
extend.** `class GetPodLogs(Tool)` overrides `invoke`, which is the one method
a business writes. A `Role` and a `Skill` are subclassed for the same reason a
C++ class with no virtual methods still gets one: to be a named unit with a
constructor of its own, so that twenty skills are twenty things a project can
name, reuse and give a common base.

**Encapsulation keeps the disclosure decision local.** Every node decides for
itself what its routing card says and what its opened form says, so changing
what a Role discloses is an edit to `Role` and to nothing else. What that buys
is locality, not extensibility: the three kinds are a closed set, and a fourth
is a breaking change to the framework rather than something a business adds.

**Polymorphism is what makes progressive disclosure uniform.** Role, Skill and
Tool have nothing else in common, yet one call — `node.compile(level, view=…)` —
serves all three and never asks what it is holding. A node reaches what it
cannot work out for itself, its own address and its own schema, by asking the
view it is compiled against; that is the whole of what the tree contributes.
Opening a node is therefore one line with no mention of any kind, which it was
not before ADR 014.

**Composition, not inheritance, models the team.** Subclassing states what one
node *is*; it never states containment. A role that coordinates other roles
builds them in `children`, inside its own constructor.

That last point is what fixes *when* anything exists. A member list is built by
the constructor that holds it, so **importing a module full of declarations
constructs nothing**: a class is a zero-argument factory, and a
`ControllerManager` calling one is the single moment a node comes into
existence — which is also the only moment it can be told where it hangs and
handed what it may reach.

**Nothing is inferred.** Not the node name from the class name, and not the
routing description from the docstring. Both were once derived and both are
dead ends for a framework meant to exist in more than one language: a
TypeScript bundler renames classes, and no Go or TypeScript runtime can read a
doc comment. The one thing still read off the code is a tool's input schema,
derived from `invoke`'s type hints — and even there, what conformance pins is
the schema that reaches the wire, never how it was derived.

Inversion of control runs the other way from a library: you never dispatch a
request, parse arguments, or serialize a result. You declare what you own,
register it, compile it into an `Index`, and hand that to a `ContextureServer`;
the framework calls your code, not the reverse.

## Three languages, one behaviour

Python is the first implementation, not the only intended one. TypeScript and
Go implementations are planned, and that is a **design constraint on this
repository from now on**, not a note about the roadmap: every feature added
here has to be one all three can carry.

**What must be identical across the three**, because it is what an agent
actually meets:

| | |
| --- | --- |
| the gateway | four tools, these names, these descriptions, these `readOnlyHint`s |
| the reference grammar | a path, one separator, no kind prefix |
| the disclosure rule | one sibling set per call; a role's members arrive on opening and never before |
| every card and payload | the exact keys of `discover`, `open`, a tool card, a `uses` card |
| every sentence said to an agent | the five lookup failures, both wrong-door refusals, the signpost, the roster's truncation line |
| the instruction budget | roster cut in whole sibling groups, roots cut last |

**What may differ**, because forcing it identical would make all three foreign
in at least two languages: how a business *authors* a declaration. The object
model is the same three kinds with the same fields; the syntax that states them
is each language's own.

### The rule this imposes

**No feature may depend on a capability only one language's runtime has.**
Reflection is where this bites, and the three runtimes do not agree:

| Needed for | Python | Go | TypeScript |
| --- | --- | --- | --- |
| Enumerating a type's members | `vars(cls)` | struct fields + tags | object properties |
| **Parameter names and types → JSON Schema** | signature | **not available** | **not available** |
| Doc comment → routing description | `__doc__` | not available | not available |

Two consequences are already settled by that table:

- **A tool's input schema is pinned at the JSON, not at how it was derived.**
  Python reads `invoke`'s signature, Go reflects an argument struct, TypeScript
  declares a schema object. The three authoring styles differ; the schema that
  reaches the wire does not, and that is what conformance tests.
- **A routing description is always written, never inferred.** Deriving it from
  a docstring works in exactly one of the three, and a field that is optional
  in one implementation and required in the others is the same declaration
  meaning two things.

### Where the shared behaviour is specified

Prose is not enough to keep three implementations saying the same sentence to
an agent — a port that quietly drops the recovery half of a failure message
still starts, still answers, and no test goes red. So the shared half is
specified as fixtures rather than described:

```text
spec/
  fixtures/       declarations, stated language-neutrally
  golden/         the exact payload and the exact sentence each one produces
  conformance.md  the reference grammar, the cut rules, the door rules
```

Each implementation runs the same golden files. Anything not in `spec/` is that
implementation's own business.

## The problem

A system that wants to work with several agents ends up maintaining the same
context several times:

```text
Claude Code   CLAUDE.md · .claude/skills/**/SKILL.md · .mcp.json
Codex         AGENTS.md · ~/.codex/config.toml
Cursor        .cursor/rules/*.mdc · .cursor/mcp.json
```

Every one of those files answers the same six questions: who is this role, what
does it know, what may it run, what may it read, what is visible by default,
and what appears only after something is selected. The answers are identical.
Only the file formats differ — and they drift apart the moment anyone edits one
of them.

A generated file is a copy. Contexture serves the answer instead, so there is
nothing to drift:

```text
Claude Code ─┐
Codex ───────┼──── MCP ────►  one Contexture server  ────►  your declaration
Cursor ──────┘
```

## Declare once

```python
from contexture import Role, Skill, Tool

class InspectPodFailure(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="inspect-pod-failure",
            description=(
                "Diagnose why a Pod is crashing, restarting, or failing to "
                "become ready."
            ),
            instructions="""
            1. Inspect the Pod status and restart count.
            2. Read current logs, then previous logs after a restart.
            3. Correlate status, logs, and events before proposing remediation.
            """,
        )

class K8sTroubleshooter(Role):
    def __init__(self) -> None:
        super().__init__(
            name="k8s-troubleshooter",
            description=(
                "Diagnose unhealthy Pods, failed Deployments, and scheduling "
                "failures."
            ),
            instructions=(
                "Start with read-only inspection. Do not modify the cluster."
            ),
            skills=[InspectPodFailure()],
            tools=[
                GetPodLogs(),
                GetEvents(),
                CrashLoopRunbook(),      # content is a read-only tool
            ],
        )
```

Which of the three lists a capability belongs in is the modelling decision this
framework asks you to make. Nothing here names an agent runtime, and nothing
here has been built yet: importing this module constructs no nodes at all —
`K8sTroubleshooter()` is called once, by the registry, when the server starts.

Building a role at run time works exactly the same way: `Role(name=..., ...)`
is what a subclass's constructor calls, and anything that accepts one accepts
the other.

## Implement what it can do

A tool is a typed Python method. Nothing writes a JSON Schema — the one in
`tools/list` is derived from this signature:

```python
from contexture import Tool

class GetPodLogs(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="get_pod_logs",
            description="Return recent container logs for one Pod.",
            read_only=True,
        )

    async def invoke(self, namespace: str, pod: str) -> str:
        return await kubernetes.logs(namespace, pod)

class CrashLoopRunbook(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="crash_loop_runbook",
            description="How to diagnose a container that keeps restarting.",
            read_only=True,
        )

    async def invoke(self) -> str:
        return RUNBOOK
```

Twenty tools of one domain that differ in three fields share a base class whose
constructor supplies the rest — ordinary inheritance, doing what it has always
done.

`read_only` is a host classification, not an argument. It is projected onto the
protocol's `readOnlyHint` so a host can ask a human first, and it never appears
in an input schema — a model that could pass its own approval flag would be
approving its own writes.

## Serve it

Most projects do not need a `main()`. Export one lazy application object from
your package:

```python
# hello_context/__init__.py
from contexture import Contexture

from .role import HelloContextAssistant

app = Contexture(name="hello-context", roots=(HelloContextAssistant,))
```

Then name that one object in `pyproject.toml`:

```toml
[tool.contexture]
app = "hello_context:app"
```

```bash
uv run contexture check                    # compile; no connections or MCP host
uv run contexture call hello-context-assistant/ping
uv run contexture serve
```

`check` catches invalid declarations before a host is involved. `call` invokes
one read-only business Tool through the same schema binding and Channels
lifecycle used in production. Pass `--allow-write` explicitly for a Tool that
can change external state.

Then point any host at that same command:

```bash
claude mcp add --scope project my-context -- uv run contexture serve
codex  mcp add                 my-context -- uv run contexture serve
```

### Reaching something outside the process

A capability that talks to a cluster, a database or somebody else's gateway
needs a handle, and a handle is a live object no configuration file can hold.
The Application names its `Channels` class directly — it is a zero-argument
factory, just like a root:

```python
from contexture import Channels

class ClusterChannels(Channels):
    def __init__(self) -> None:                 # address: cheap, no I/O
        self.url = os.environ["CLUSTER_URL"]

    async def open(self) -> None:               # the connection
        self.api = await self.enter(session(self.url))
        self.db  = await self.enter(create_pool(DSN))

    async def close(self) -> None:
        self.api = self.db = None

app = Contexture(
    name="my-context",
    roots=(MyContextAssistant,),
    channels=ClusterChannels,
)
```

`open` runs once, before the first request, so a connection that cannot be made
stops the server starting rather than failing in front of whoever asked first.
`close` runs after the last. Whatever you put on `self` is what a capability
finds on `self.channels`.

`enter` hands a resource to the framework to unwind: several handles close in
reverse, and the first is closed if the second fails to open. Contexture never
looks inside any of it — one object, opened and closed, and its contents are
yours.

`contexture serve` works unchanged. That is the point of putting the complete
composition in one declaration.

### Writing the entry point yourself

For a graph served from a process this command does not own — embedded in an
existing service, or built inside a test — write the entry point yourself. It
still consumes the same declaration; it does not reassemble a second graph:

```python
from hello_context import app
from contexture.server import ContextureOptions, serve

def main() -> None:
    serve(app, ContextureOptions(transport="stdio"))
```

Both paths compile a fresh forest through `ControllerManager → Index →
Disclosure → ContextureServer`. Importing `app` builds nothing; each root comes
into existence only during compilation, and the resulting index is frozen
before the server is created.

The fixed gateway remains framework-owned. `prompts=` and `resources=` belong
on `Contexture(...)`, where they point to existing nodes without duplicating
their business definitions.

Each Tool's card schema and invocation validation come from the same compiled
binding. The application author declares the Tool once; neither CLI nor custom
hosting derives a second version of its contract.

Nothing above imports `mcp`, writes JSON-RPC, or names an agent runtime.

Already have an event loop? Build it from the same app with
`build_server(app)`, then `await server.start_async(options)`.

### Over the network

The same graph, served over Streamable HTTP. Both protocol eras are answered by
one process: the 2026-07-28 revision, which has no handshake, and the older
revisions that still negotiate one.

```bash
uv run contexture serve --transport streamable-http --port 8080
```

Beyond loopback, two things have to be typed rather than defaulted, because the
SDK stops protecting an address it does not recognise as local and nothing
would say so:

```python
build_server(app).start(ContextureOptions(
    transport="streamable-http",
    host="0.0.0.0",
    port=8080,
    allowed_origins=["https://acme.example"],
    auth=Auth(verifier=OktaVerifier(), issuer=..., resource=...),
))
```

Sessions are not offered. There is no protocol session since 2026-07-28, this
server keeps no per-connection state, and a ref resolves the same way for
everyone — so any replica can answer any request, and nothing behind a load
balancer needs to be sticky.

## Layers

```text
your application            subclasses Role/Skill/Tool, constructs Prompt/Resource
        │  one import: `from contexture import ...`
core.model                  the kernel — the object model, the forest, and the
        │                   four calls an agent makes; no wire, no SDK
        │
core.mcp_interface          what each MCP primitive carries — still no SDK
        │  bind
contexture.server           the only layer importing mcp
        │  run
contexture.cli            `contexture new` scaffolds, `contexture serve` runs
        │  MCP
Claude Code · Codex · Cursor · any MCP host
```

Each layer may import the ones below it and never the reverse. `core` in
particular must not import `mcp`: an object model that reaches for a wire
protocol has stopped being an object model. `tests/test_layering.py` enforces
this in the AST and again at runtime, in a subprocess, so a convenient import
fails rather than quietly reshaping the package.

## Progressive disclosure

Every `ContextNode` — role, skill, tool — answers the same two questions:

```python
node.compile("route")   # what is this, and when should it be picked?
node.compile("active")  # the detail, now that it has been picked
```

**One call shows one level.** Every axis is lazy — the role axis included:

```text
contexture_discover()        → the root roles, one card each
contexture_open(role)        → its instructions, and a card for each sub-role,
                               skill and tool it holds — tools with the
                               schema needed to call them
contexture_open(skill)       → the full procedure, here and nowhere else
```

A level always arrives whole, because choosing between siblings requires seeing
all of them: what cannot be seen together is guessed between rather than chosen
between. It does not follow that every level should arrive at once. Entering a
server costs the number of roots, and a branch costs what is on the way down
it — so a forest of eleven thousand roles is as cheap to enter as one of three.
[ADR 007](docs/adr/007-the-role-axis-is-lazy-too.md) has the measurements, and
what it cost to get this wrong first.

The one obligation this puts on a declaration: **a role's description has to
route for its whole subtree**, because an agent choosing among siblings cannot
see the grandchildren.

A roster of roles also ships inside the server's instructions, because a gateway
whose four tool names all begin `contexture_` otherwise tells a host nothing
about what the server is for. That roster costs no round trip, so it keeps going
while there is budget — breadth-first, so a forest too large for it is cut after
the levels that route rather than after one deep spine.

Each card carries the `ref` that opens it — a path, like
`kubernetes-platform/incident-response/get_pod_logs`. **That ref is the agent's
position, and the server does not remember it**, which is what makes traversal
legal: since the 2026-07-28 revision MCP has no protocol session, and a server
may not vary its tool list per connection or as a side effect of earlier calls.
Opening a ref is a pure function of that ref. An agent never assembles one:
cards are built by a function that takes the reference as an argument, so a card
that can be seen can always be opened.

There is no exception to this. Every node — role, skill, tool — answers the
same two questions, which is what lets one function walk all of them. Content
that is simply *there* is a read-only tool taking no arguments: its card costs
a line, and the document arrives only when the tool is run.

## Disclosure is not authorization

These are separate, and conflating them is the trap this design is built to
avoid.

**What disclosure controls is knowledge.** A procedure, its ordering, and its
constraints arrive only from `contexture_open`. Nothing stops an agent from
guessing a path and calling a tool without ever navigating — references are
readable, deliberately — and nothing should pretend to. An agent that skips
ahead can run a tool; it cannot know what the runbook says about exit code 137,
or that restarting first repairs nothing.

**Authorization stays with the host**, which the specification already makes
responsible for keeping a human in the loop. With capabilities off the surface a
host can no longer be told per tool whether to ask, so `read_only` becomes
*which door was used*:

```text
contexture_invoke_read_only    readOnlyHint: true    a host may allow it
contexture_invoke              readOnlyHint: false   a host may ask a human
```

A model can pick the wrong door. Picking it gets the call **refused rather than
executed**, because the host made its decision from the hint on the entry point.
That is the same protection as never letting the classification be an argument,
relocated to where the host can still act on it.

### Identity is given to you; the decision is not

Over HTTP a caller arrives with a bearer token, and Contexture does exactly
three things with it: answers `401` with the pointer a client needs to go and
get a real one, publishes the protected-resource metadata that pointer leads
to, and hands whatever your verifier made of it to your code.

```python
from contexture import Tool, current_principal

class RollBackDeployment(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="roll_back_deployment",
            description="Restore a Deployment's previous revision.",
            read_only=False,
        )

    async def invoke(self, namespace: str, deployment: str) -> str:
        who = current_principal()
        if who is None or "k8s.write" not in who.scopes:
            raise PermissionError(f"{who and who.subject} may not roll back.")
        ...
```

The verifier is yours too — one method, and no `mcp` import in sight:

```python
class OktaVerifier:
    async def verify(self, token: str) -> Principal | None:
        ...   # your IdP's library, your audience check
```

**No verifier ships with this package**, and it will not issue tokens. A
built-in verifier arrives with defaults, defaults get copied into production,
and a wrong default in OAuth is a vulnerability rather than a nuisance.

There is no `required_scopes` on a tool and no visibility filtering, for the
same reason: which caller may do what is a question about your system, and a
framework that answered it would be answering for you. The consequence is worth
stating outright — **a caller who cannot run a capability can still see its
card.** Disclosure controls knowledge; it has never controlled permission, and
this is that sentence applied to callers rather than to agents.

Over stdio `current_principal()` is always `None`. Nobody authenticated, the
host launched the process, and the operating system already decided who that is.

## Publish explicit REST routes for a human View

MCP progressively discloses the Controller tree so a model can decide where to
go. A dashboard has already made that navigation decision in its pages and
buttons, so its HTTP surface is an explicit allowlist of stable routes instead:

```python
from contexture.server import compile_application
from contexture.web import RestSurface, Route
from my_context import app

compiled = compile_application(app)
rest = RestSurface(compiled.runtime(), routes=(
    Route("GET", "/v1/projects", "project/list-projects"),
    Route("POST", "/v1/projects", "project/upsert-project", status=201),
))
asgi_app = rest.asgi_app()
```

Mount `asgi_app` in an ASGI server or application. A `Route` is only an HTTP
address pointing at a Tool the compiled tree already owns; it is not a fourth
Controller kind and it is intentionally not exported from `contexture`.
Unpublished Tool refs are unreachable over REST, and construction refuses a
GET/HEAD route pointing at a writing Tool or a writing route pointing at a
read-only Tool.

Pass an `authenticate(WebRequest) -> Principal | None` callback when the REST
surface is protected. Contexture binds the returned identity for exactly one
Tool call; the capability continues to make its own permission decision with
`current_principal()`. Request headers are never interpreted as a Principal by
the framework.

## Quick start

Python 3.10 or newer.

```bash
uv tool install contexture-mcp
contexture new my-context
cd my-context
uv sync
uv run contexture check               # build and validate without connections
uv run contexture list
uv run contexture inspect my-context-assistant/check-target
uv run contexture call my-context-assistant/ping --input '{"target":"example.test"}'
uv run contexture serve               # serve it over stdio
```

The generated project holds declarations and nothing else. It has no entry
point, no `main()`, and no console script of its own, because the framework
ships the runner:

```text
my-context/
├── pyproject.toml       [tool.contexture] app = "my_context:app"
├── my_context/
│   ├── __init__.py      app = Contexture(...)
│   ├── role.py          the responsibility boundary
│   ├── skills.py        procedures the model follows
│   └── tools.py         typed capabilities Contexture runs
└── uv.lock              created by uv sync
```

The generated package exports one `app`. It starts with Role, Skill, and Tool
because all three are first-class concepts: a Skill tells a model how to work;
a Tool is deterministic work that Contexture invokes.

It is a project, not a package: no build system, never installed into the
environment. `contexture serve` finds it by walking up for the
`[tool.contexture]` table and putting that directory on the *end* of
`sys.path` — behind the standard library, so a module of yours named after one
of Python's own cannot answer for it.

Then point a host at it — the same command for each:

```bash
claude mcp add --scope project my-context -- uv run contexture serve
codex  mcp add                 my-context -- uv run contexture serve
```

Add child Roles, Skills, and Tools in `my_context/`; add Channels, Prompt, or
Resource declarations to the same `app` only when the problem requires them.
Continue with the step-by-step [Contexture Handbook](docs/handbook.md).

### From a checkout

```bash
git clone git@github.com:CarterShi01/Contexture.git && cd Contexture
uv sync
uv run contexture new ~/my-context
uv run contexture demo            # the bundled reference application, over stdio
uv run python run_tests.py        # the full suite
uv run contexture inspect --all --summary
```

That last command prints what an agent receives at every node, and what it
costs, without an agent in the room. Run outside a project it replays the
bundled demo, which is what makes it useful from this checkout — the framework
has no `[tool.contexture]` project of its own.

See [`contexture/demo/`](contexture/demo/) for
that reference application — a deterministic Kubernetes incident that forces the
whole traversal — and [`docs/verification/hosts.md`](docs/verification/hosts.md)
for a recorded run against both hosts.

## Project layout

```text
contexture/
├── core/
│   ├── model/       the kernel: node, role, skill, tool, manager, tree, and
│   │                system_api — what a capability is, where it hangs, how
│   │                much arrives per call, and the four entry points
│   └── mcp_interface/  what each of MCP's three primitives carries; a
│                    business extends prompt and resource, never tool
│   └── errors.py, types.py, constants.py — shared by both
├── web/             explicit Route declarations and the REST/ASGI Host adapter
├── server/          the MCP server: messages (what is said to somebody),
│                    instructions (fitting it to a host), binding (hanging the
│                    surface on the SDK), app, launch
├── inspection.py    replaying the disclosure for a developer to read
├── cli/             the `contexture` command: scaffold (writing a project),
│                    project (finding and resolving one), main (the five
│                    commands), and the templates `new` renders
└── demo/            the bundled reference application, on the public API only
```

`contexture/` is the whole of what ships. The two directories beside it —
`tests/` and `docs/` — and the `run_tests.py` that drives one of them are for
working *on* the framework and reach no user's machine, which
`pyproject.toml` states by naming the package to include rather than by
listing what to leave out. `PackagingBoundaryTests` in
`tests/test_layering.py` is where that stops being a convention.

`docs/` holds four different things and its directories say which is which:
`adr/` is the append-only decision history, `verification/` is what a real
host actually did and how to make it do that again, `case-studies/` is a
domain rebuilt on this framework and written up, and `atlas/` is the offline
visual map.

## Design documents

- [`docs/01-role-object-model.md`](docs/01-role-object-model.md) — the object
  model, its invariants, and why each boundary sits where it does.
- [`docs/02-framework-layers.md`](docs/02-framework-layers.md) — the framework
  shape: declaration, compilation, and the server.
- [`docs/05-controller-framework-and-mvc.md`](docs/05-controller-framework-and-mvc.md)
  — the corrected MVC mapping: the Host is the View, Contexture is the whole
  Controller layer, and the business application owns the Model.
- [`docs/06-multiple-host-surfaces-plan.md`](docs/06-multiple-host-surfaces-plan.md)
  — how MCP and explicit REST routes share one Controller runtime without adding
  a fourth node kind.
- [`docs/adr/013-a-constructor-is-the-declaration.md`](docs/adr/013-a-constructor-is-the-declaration.md)
  — why a class body is no longer read, what the twenty never-served objects it
  built at import were, and the three-language table that forced it.
- [`docs/adr/001-native-mcp-server.md`](docs/adr/001-native-mcp-server.md) — why
  the main path became a server, what it cost, and what was deliberately left
  alone.
- [`docs/adr/003-remove-the-outbound-half.md`](docs/adr/003-remove-the-outbound-half.md)
  — why the client half ADR 001 left alone was removed instead, and what that
  gave up.
- [`docs/adr/004-progressive-disclosure-as-a-lazy-role-tree.md`](docs/adr/004-progressive-disclosure-as-a-lazy-role-tree.md)
  — why capabilities left the MCP surface for a fixed gateway, why
  disclosure splits by kind rather than depth, and why the class design that
  holds it is one class rather than the seven the first draft proposed.
- [`docs/adr/005-remove-the-target-adapters.md`](docs/adr/005-remove-the-target-adapters.md)
  — why the file-rendering side road ADR 001 demoted was deleted rather than
  kept, and what a user would have to be able to type for it to come back.
- [`docs/adr/006-errors-carry-facts-and-the-contract-is-one-module.md`](docs/adr/006-errors-carry-facts-and-the-contract-is-one-module.md)
  — why a failed lookup carries facts instead of a sentence, why everything an
  agent reads moved into one module, and why `Role` took back its own lookup.
- [`docs/adr/009-the-protocol-plane-is-not-the-object-model.md`](docs/adr/009-the-protocol-plane-is-not-the-object-model.md)
  — why `core.Resource` was deleted, why the three MCP primitives are declared
  in one directory that imports no SDK, and why `opened_by` moved out of the
  object model.
- [`docs/adr/010-the-directories-are-the-architecture.md`](docs/adr/010-the-directories-are-the-architecture.md)
  — the move itself, and why the layering test had to be hardened before a
  single file could be moved.
- [`docs/adr/014-navigation-is-part-of-the-kernel.md`](docs/adr/014-navigation-is-part-of-the-kernel.md)
  — why the four entry points, the forest and the nodes ended up in one
  directory, why a node now hands out addresses it cannot spell, and why
  registration and disclosure stayed two types rather than one.
- [`docs/adr/007-the-role-axis-is-lazy-too.md`](docs/adr/007-the-role-axis-is-lazy-too.md)
  — why the role skeleton stopped being delivered whole: the argument for it
  was about one level of siblings and got applied to all of them, which is
  free at six roles and does not fit in a context window at eleven thousand.
- [`docs/adr/011-identity-is-the-frameworks-permission-is-not.md`](docs/adr/011-identity-is-the-frameworks-permission-is-not.md)
  — why serving is stated as an options object rather than passed through as
  keyword arguments, why the framework carries a caller's identity but ships no
  verifier and no policy vocabulary, and what that leaves visible to a caller
  who may not act.
- [`docs/atlas/index.html`](docs/atlas/index.html) — an offline visual atlas;
  open it directly in a browser. After editing it, run
  `npm install jsdom@22 && node docs/atlas/check.mjs` to confirm every diagram
  still parses; a mermaid syntax error otherwise stays invisible until someone
  opens the page.

## What Contexture does not do

Three things sit next to Contexture and are easy to confuse with it. Each
boundary is deliberate, and each one is what keeps this codebase small.

### Not the MCP SDK — it sits on top of one

The official `MCPServer` is a protocol implementation: it turns a Python
function into a legal MCP tool, derives JSON Schema from type hints, frames
stdio, and negotiates protocol versions. Contexture does none of that and never
will.

| | `mcp.server.mcpserver.MCPServer` | Contexture |
| --- | --- | --- |
| Audience | protocol implementers | business developers |
| Question it answers | how does this function become a legal MCP tool? | how does this domain become a graph an agent can navigate? |
| Unit | a flat list of tools, resources, prompts | a forest of roles with boundaries |
| Scale it assumes | a dozen tools, all resident | hundreds of capabilities, most of them out of context |
| Context budget | not its problem | a first-class constraint |
| Wire format, schema, transport | owns MCP | delegates MCP to the SDK; owns its small REST/ASGI adapter |

The dependency stays confined to `contexture/server/` and a small set of SDK
entry points: the constructor and the two async runners; `add_tool`,
`add_prompt`, `add_resource` and `completion` for registration; and
`Tool.from_function`, `Prompt.from_function` and `FunctionResource.from_function`
for deriving what each one carries off the wire. `ContextureServer`
composes an `MCPServer` rather than subclassing one, so an SDK upgrade cannot
reach into the object model, and `tests/test_layering.py` fails if any other
layer imports `mcp`.

One thing the SDK offers and Contexture declines: **the decorator style
(`@server.tool()`).** Capabilities are runtime objects walked out of a graph,
not functions known at import time in one module.

All three primitives are used, and split the way the protocol splits them — by
**who decides when one is used**. Roles, skills and tools reach a model through
the tool primitive. A `Prompt` is not a Skill wearing a protocol's clothes: it
names a node a *person* triggers, and declaring one is worth it only where
going wrong is expensive. What each primitive carries is declared in
[`core/mcp_interface/`](contexture/core/mcp_interface/README.md), which
imports no SDK — that is `server`'s job. You do not need that path to write
one: `Prompt` and `Resource` sit on the `contexture` facade beside the three
node kinds, because which plane a thing belongs to is a modelling decision and
not a question about this package's directories.

### Not the agent runtime — Claude Code and Codex stay in charge

Contexture has no planner, no agent loop, no tool selection, and never calls a
model. It does not decide which skill fits a task. It makes that decision
cheaper by putting a one-line routing card in front of the model where a full
procedure would otherwise sit.

The division is sharpest around authorization. **Disclosure is not
authorization**: Contexture governs what an agent *knows*, the host governs what
an agent may *run*. A tool's `read_only` classification decides which of the two
invoke doors it must be run through, and each door carries the matching
`readOnlyHint`, so the host can still ask a human first. Contexture itself never
asks and never blocks — it only refuses a call whose door disagrees with its
ref, because the host made its decision from that door.

Host configuration is not Contexture's either. It emits the launch command
through `contexture.server.launch` and stops there; what the host does
with sampling, permissions, or its own memory files is outside the boundary.

### Not your business system — it describes one, it does not become one

Contexture holds none of your state. No database, no cache, no queue, no
scheduler, no retry policy, no transactions, and no domain logic of its own. A
`Tool` is a typed Python method whose body is *your* code calling *your*
system; Contexture decides when the description of that method becomes visible,
and hands the call through.

These stay yours:

- **Business logic and persistence.** `invoke` and `read` are your call sites,
  and the framework never inspects what happens inside them.
- **Credentials for your own backends.** Contexture stores none.
- **What a procedure actually says.** A Skill's `instructions` are domain
  knowledge the framework never authors, validates, or rewrites.
- **Calling somebody else's MCP server.** Contexture is the inbound half. Use
  the SDK's client for outbound work; the hand-written client that once lived in
  `contexture.protocol` was removed rather than maintained against a moving
  specification, for the reasons in
  [`docs/adr/003-remove-the-outbound-half.md`](docs/adr/003-remove-the-outbound-half.md).

Consistency under concurrency is on that list too, and deliberately. Locks,
transactions, leases, and idempotency keys belong where the data is. A
framework-supplied lock would be a weaker duplicate of the one your database
already has, and a misleading one, because Contexture cannot see the writes it
would be claiming to order.

### One rule this does leave you: declared objects are shared

Holding none of your state does not mean holding none of your objects.
Contexture builds each Role, Skill and Tool once, when the tree is
built, and every call reaches that same instance — from every session, and from
the parallel calls a single host issues on one connection, which the SDK
dispatches as concurrent tasks.

Two things follow, and they are the whole of the contract:

- **`invoke` and `read` must be re-entrant.** Keep a call's state in its
  arguments and its locals, never on `self`. Nothing enforces this: a Tool
  subclass carries a `__dict__`, so `self.pending = ...` succeeds quietly and
  is then shared with every other call in flight.
- **Do not change a role's members once it is serving.** Assemble the graph at
  runtime if you like — that is what the imperative constructors are for — but
  an `append` to `role.tools` on a live server varies the surface as a
  consequence of an earlier call, which the 2026-07-28 revision forbids.

Contexture holds up its own end: the four gateway tools answer purely out of
the declaration, and `StatelessnessTests` in `tests/test_binding.py` compares
a server that has served every call it will ever see against one that has served
none.

### Also true

- Not a new protocol. It speaks MCP through the official SDK rather than its own
  JSON-RPC implementation.
- Not zero-dependency any more. Serving MCP means depending on `mcp`, and that
  is a deliberate trade recorded in ADR 001.

## License

Apache-2.0
