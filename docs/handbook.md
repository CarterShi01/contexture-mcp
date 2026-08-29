# Contexture Handbook

This is the complete onboarding path for a Contexture application. Follow it
in order the first time:

```text
install → generate → understand → check → inspect → call → extend → connect a host
```

You do not need to understand `ControllerManager`, `Index`, or MCP wire
protocol details before completing this path. You also do not need to write a
`main()`.

## 1. The four concepts

Every application starts with exactly four concepts:

| Concept | Question it answers | First file |
| --- | --- | --- |
| `Contexture` / `app` | What makes up this application? | package `__init__.py` |
| `Role` | Which stable responsibility should the agent enter? | `role.py` |
| `Skill` | Which procedure should an agent follow? | `skills.py` |
| `Tool` | Which deterministic action should code perform? | `tools.py` |

Role, Skill, and Tool are equally first-class. A Role owns a stable
responsibility boundary, whether that responsibility is online business work,
scheduling, messaging, infrastructure, or another system concern. A Skill gives
an agent a procedure to follow; Contexture does not execute it. A Tool is typed
Python code that Contexture does execute. Runtime records such as processes,
jobs, and connector instances remain Tool-returned data rather than Roles.

There is one Disclosure for all of them. Opening a Role shows one containment
level, and explicit `uses` cards show declared dependencies; it never runs a
health probe or reveals reverse edges from unentered branches. Whole-graph
introspection belongs behind an application Tool. Health and other live domain
state belong to read-only Tools. Contexture separately records the
minimal call count, error count, and last-use time for each entered Role/Skill
and invoked Tool; that Telemetry is runtime evidence, not Agent context.

## 2. Install and generate a project

Prerequisites: Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install contexture-mcp
contexture --version
contexture new hello-context
cd hello-context
uv sync
```

The generated project is intentionally small:

```text
hello-context/
├── pyproject.toml
├── README.md
└── hello_context/
    ├── __init__.py       # exports the one app
    ├── role.py           # responsibility boundary
    ├── skills.py         # procedures for an agent
    └── tools.py          # executable capabilities
```

This is an application served by Contexture, not a Python package you need to
publish. It has no build system, its own console script, or a required
`main.py`.

## 3. Understand the generated application

`hello_context/__init__.py` is the one composition root:

```python
app = Contexture(name="hello-context", roots=(HelloContextAssistant,))
```

That declaration is lazy: importing it does not build nodes, open a connection,
or start a server. Contexture consumes it later when a command runs.

The generated Role owns both a Skill and a Tool:

```python
skills=[CheckTarget()],
tools=[Ping()],
```

The Skill names the Tool it needs with `uses=("hello-context-assistant/ping",)`.
`Ping.invoke(target: str)` is the business code. Its parameter name and type
are also used to derive the MCP input schema, so no second schema needs to be
maintained.

## 4. Complete the local development loop

Run these commands from the project root before connecting any MCP host:

```bash
uv run contexture check
uv run contexture list
uv run contexture inspect hello-context-assistant/check-target
uv run contexture call hello-context-assistant/ping --input '{"target":"example.test"}'
```

Expected outcome:

- `check` prints `OK hello-context: 1 role(s), 1 skill(s), 1 tool(s)`. It
  builds and validates the application but does **not** open external connections.
- `list` prints every Role, Skill, Tool, and its ref.
- `inspect` shows the instructions an agent receives when it opens that Skill.
- `call` prints `example.test: reachable (placeholder)`. It uses the same Tool
  binding as the served application, without starting MCP.

Use the commands for distinct questions:

| Question | Command |
| --- | --- |
| Can this app compile? | `contexture check` |
| What refs exist? | `contexture list` |
| What will an agent see? | `contexture inspect` |
| What does one Tool return? | `contexture call REF --input JSON` |

Do not use `serve` as the first way to debug a Tool.

## 5. Make your first change

Replace the placeholder body in `hello_context/tools.py`:

```python
async def invoke(self, target: str) -> str:
    return f"{target}: connected"
```

Then repeat the two fastest checks:

```bash
uv run contexture check
uv run contexture call hello-context-assistant/ping --input '{"target":"api.internal"}'
```

The result should now be `api.internal: connected`. If you add or change a
Tool argument, change the `invoke()` signature and rerun `check`; the binding
and schema are rebuilt from that one declaration.

## 6. Add capabilities in the right place

Use this decision rule:

- Add a **Tool** when code can deterministically perform the work and return a
  result. Define it in `tools.py`, then add an instance to the owning Role's
  `tools=[...]`.
- Add a **Skill** when an agent needs a procedure, ordering, evidence rules, or
  judgment. Define it in `skills.py`, add it to `skills=[...]`, and use `uses`
  to name the Tools it needs.
- Add a child **Role** only when an agent must choose among distinct business
  responsibilities. Add it to the parent's `children=[...]`.

After changing the graph, verify it:

```bash
uv run contexture check
uv run contexture list
uv run contexture inspect --all --summary
```

Take refs from `list` or cards returned by `inspect`; do not construct them by
guessing.

## 7. Connect external systems only when needed

When Tools need a database, HTTP client, cluster, or similar shared dependency,
add a `Channels` subclass and name it on the same application declaration:

```python
app = Contexture(
    name="operations",
    roots=(Operations,),
    channels=OperationsChannels,
)
```

`Channels.__init__()` records cheap configuration. `open()` establishes
connections, and `close()` releases them. `check` does not call `open()`;
`call` and `serve` use the full production lifecycle. Once Channels exists,
use a read-only `call` to verify the connection and cleanup path.

## 8. Publish optional entrances

Most applications need only Roles, Skills, and Tools. Add these concepts only
when their specific entry point is needed:

- `Prompt`: a person-triggered entry point.
- `Resource`: a stable URI a host can read.

Both point at nodes your tree already owns. They do not duplicate Tool or Skill
business logic:

```python
app = Contexture(
    name="operations",
    roots=(Operations,),
    prompts=(RollbackRelease,),
    resources=(OperationsRunbook,),
)
```

## 9. Connect an MCP host

After the local loop succeeds, configure the host to run the same command:

```bash
claude mcp add --scope project hello-context -- uv run contexture serve
codex mcp add                 hello-context -- uv run contexture serve
```

For stdio, do not manually leave `contexture serve` running in a separate
terminal before adding the host. The host launches and owns that process for
its session. Running it yourself is useful only when debugging the transport:

```bash
uv run contexture serve
```

An agent first receives Role routing cards, then opens exactly the branch it
needs. Business Tools are not flattened into MCP's top-level tool list;
Contexture serves a fixed gateway and progressively discloses the business graph.

## 10. Local safety and troubleshooting

`call` runs only read-only Tools by default. To call a Tool that can modify an
external system, you must make the decision explicit:

```bash
uv run contexture call REF --input '{...}' --allow-write
```

`call` does not invent a remote caller identity. Test authentication,
`Principal`, and host-specific behavior through a real server integration.

Common recovery paths:

| Symptom | Next action |
| --- | --- |
| `check` fails | Fix the named declaration or ref, then rerun `check`. |
| A ref is unknown | Run `contexture list`; inspect the owning Role. |
| A Role or Skill was passed to `call` | Run `contexture inspect REF`; only Tools can be called. |
| A writing Tool is refused | Review the intended effect, then add `--allow-write`. |
| An external call fails | Check Channels configuration and test one read-only Tool locally. |

Diagnostics are written to stderr while Tool results go to stdout, so `call`
output remains usable in scripts.

## 11. Deploy or embed

Start with stdio. For Streamable HTTP, the declaration remains unchanged:

```bash
uv run contexture serve --transport streamable-http --port 8080
```

For a public address, explicitly configure host/origin and authentication policy
with `ContextureOptions`. Those are deployment choices, not Tool fields.

Write a custom `main()` only when embedding in an existing process, using an
existing event loop, or selecting hosting options in code. It still consumes
the same app:

```python
from hello_context import app
from contexture.server import ContextureOptions, serve


def main() -> None:
    serve(app, ContextureOptions(transport="stdio"))
```

For an existing event loop, use `build_server(app)` and await the server's
async start method. Ordinary applications should not hand-assemble
`ControllerManager`, `Index`, or `ContextureServer`.

## 12. Completion checklist

- [ ] `contexture new` created a project.
- [ ] `uv run contexture check` succeeds.
- [ ] `contexture call` returned a value from your own Tool code.
- [ ] You can distinguish Role, Skill, and Tool.
- [ ] You know where to register a new capability in its owning Role.
- [ ] You have checked refs and agent-visible text with `list` and `inspect`.
- [ ] A writing Tool is only locally executed with `--allow-write`.
- [ ] Your host launches `uv run contexture serve` successfully.

## Internals and design decisions

The runtime path is:

```text
Contexture → ControllerManager → Index → Disclosure → ContextureServer
```

These are framework internals. `ControllerManager` owns constructed nodes and
Channels; `Index` holds compiled addresses and Tool bindings; `Disclosure`
decides what an agent receives. Read [the ADRs](adr/) only when you need to
work on Contexture itself rather than build an application with it.
