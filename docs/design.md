# One model for application structure and agent interaction

[简体中文](design.zh-CN.md) · [Documentation](README.md) · [Handbook](handbook.md)

Contexture is an application-layer Controller framework. Its central choice is
to use the same capability declarations to organize an application and expose
an interface that an agent can navigate. Progressive disclosure is one result
of that structure, not the whole purpose of the framework.

## The application-design problem

MCP standardizes access to tools and data. A growing application still needs
decisions about responsibility boundaries, reusable procedures, executable
actions, and the guidance an agent receives in each area. These are
application-design concerns, not gaps in the MCP protocol.

If those relationships exist only in separately maintained descriptions and
prompts, changes are harder to trace and check. Contexture makes them explicit
in application code, without moving business logic or authorization into a
new agent runtime.

## Encapsulate responsibilities; compose capabilities

![Role internals beside an application composed of recursive Roles.](media/contexture-overview.png)

- A **Role** names a responsibility and holds local instructions, Skills,
  Tools, and child Roles. Child Roles introduce more specific responsibilities.
- A **Skill** describes a procedure for the model to follow. Its `uses` refs
  connect the procedure to existing capabilities; it is not executable workflow code.
- A **Tool** binds an executable handler to a typed input schema. It can call
  an existing service rather than reimplement its domain logic.
- **Containment** establishes ownership and canonical paths. **`uses`** records
  explicit references; it does not schedule calls, provide retries, or create
  transactions. Containment is composition, not instruction inheritance.

The compiler builds an immutable graph and checks declared relationships and
bindings. That compiled model supports both the agent-facing disclosure
payloads and the runtime that resolves and invokes Tools.

## What the agent receives

| Interaction | What it does | What it does not do |
| --- | --- | --- |
| Discover | Return starting-point cards | Load the whole graph's instructions |
| Inspect | Compare candidate routing cards | Activate instructions or expose Tool schemas |
| Open | Disclose local guidance and capability cards; opening a Skill gives its procedure and declared references | Execute business code |
| Invoke | Resolve, validate, and execute a Tool through the appropriate read/write door | Enforce every instruction in a Skill |

An opened Role includes one level of members; Tool cards provide the schema
needed to call them. The agent can choose a relevant branch without opening
unrelated detail. This is not a mandatory state machine: invoking a known ref
does not require a prior open. Opening does not automatically inherit ancestor
instructions or evict material from the host's context. Disclosure is not authorization.

## Why this can help development and maintenance

The aim is to keep responsibility boundaries, reusable guidance, and executable
capabilities together as an application evolves:

- Add or refine a procedure by referencing existing Tools, rather than copying
  their implementations into another integration.
- Inspect the context generated from the same declarations that the application
  compiles, rather than maintain a separate hand-written navigation catalog.
- Generate a Tool's schema and validate its calls from the same typed handler.
- Expose selected Tools through explicit REST routes using the same runtime,
  while business services retain their logic and authorization.

These are mechanisms, not measured claims about development hours or ROI.
Graph validation checks structure, not the correctness of prose or model
decisions. References use explicit paths: moving a capability may require
updating its `uses` refs and callers. See the existing
[extension guide](handbook.md#6-add-capabilities-in-the-right-place).

## Relationship to other MCP frameworks

[FastMCP](https://gofastmcp.com/servers/composition) supports service composition,
[Skills as resources](https://gofastmcp.com/servers/providers/skills), and
[on-demand tool search](https://gofastmcp.com/servers/transforms/tool-search).
[mcp-use](https://github.com/mcp-use/mcp-use) supports building and integrating
MCP services and apps. [FastAPI-MCP](https://github.com/tadata-org/fastapi_mcp)
reuses existing FastAPI endpoints. These are useful foundations, not weak
baselines that only expose an unstructured list of functions.

Contexture makes a specific architectural choice: a recursive responsibility,
procedure, and action model is the basis for composition, checked references,
navigation, guidance disclosure, and Tool invocation. Similar behavior can be
built with other frameworks; Contexture provides this integrated model as its
default authoring approach. It does not claim exclusive ownership of OOP,
modularity, service reuse, or progressive disclosure.

## When to choose it—and when not to

Consider Contexture when several application responsibilities need their own
guidance and reusable capabilities, or when developers want an explicit model
whose structure they can inspect alongside the agent interface.

Direct MCP tools may be simpler for a small set of independent actions. Host
tool search may already address a discovery-only problem. A workflow engine is
a different choice when code must enforce ordering, retries, or transactions.

The costs are real: navigation adds round trips; descriptions and Skills need
careful authoring; the fixed gateway hides per-business-tool names and schemas
from the top-level MCP list. Hosts may therefore need ref-aware integration
for per-tool search or approval policies. The two read/write doors help with
classification, but are not a replacement for business authorization.

Start with the [deterministic demo](../contexture/demo/README.md), then use the
[handbook](handbook.md) to build your own application. Future visualization and
evaluation work is described separately in the [roadmap](roadmap.md).
