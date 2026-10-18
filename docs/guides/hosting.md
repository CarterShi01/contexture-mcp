# MCP and REST hosting

[简体中文](hosting.zh-CN.md) · [Documentation](../README.md) · [Authoring](authoring.md)

## Connect an MCP host

After the [quickstart](../../README.md#quickstart), run **one** registration
command from the generated project directory. The host owns the stdio process.

```bash
# Claude Code: project-scoped configuration
claude mcp add --scope project hello-context -- uv run contexture serve

# Or Codex: updates user configuration
codex mcp add hello-context -- uv run contexture serve
```

Start the chosen host from this project, check its MCP connection status, and
ask it to use `hello-context`. To remove the connection later, remove only the
named entry; preserve other configured servers. See the
[connection tutorial](../handbook.md#9-connect-an-mcp-host).

## Gateway and disclosure

The standard runtime server exposes five fixed model-controlled tools:

```text
contexture_discover
contexture_inspect
contexture_open
contexture_invoke_read_only
contexture_invoke
```

Discover returns starting points. Inspect compares routing cards without
loading instructions or Tool schemas. Opening a Role returns local guidance
and one level of member cards, including schemas for its Tools. The two invoke
doors classify read-only and writing calls; a call through the wrong door is
refused. Business authorization remains with the application and host.

An optional disclosure-only server provides discovery, inspection, and
opening without invocation. See [deployment and embedding](../handbook.md#11-deploy-or-embed).

## Streamable HTTP

```bash
uv run contexture serve --transport streamable-http --port 8080
```

Non-loopback HTTP requires an explicit authentication or anonymous-access
decision plus allowed hosts/origins. Configure these with `ContextureOptions`;
see [local safety](../handbook.md#10-local-safety-and-troubleshooting).

An HTTP deployment can select complete subtrees with `Contexture-Select` and
`HeaderSurfaceSelector`. Exact refs select one subtree; `team/*` selects each
direct member subtree, and `*` never crosses `/`. Legacy `Contexture-Roots` and
`HeaderRootSelector` names remain compatible. A ceiling derived from the
verified `Principal` can restrict the selection further. Selection does not
replace permission policy.

The following fragment assumes `app` is your [application declaration](authoring.md):

```python
from contexture.server import HeaderSurfaceSelector, compile_application

server = compile_application(app).server(
    surface_selector=HeaderSurfaceSelector(),
)
```

## Human-facing REST routes

An agent benefits from navigation; a dashboard has already chosen its pages
and buttons. Expose an explicit Tool allowlist over the same runtime.
Here `my_context` represents the package exporting the `operations` example's `app`:

```python
from contexture.server import compile_application
from contexture.web import RestSurface, Route
from my_context import app

compiled = compile_application(app)
rest = RestSurface(
    compiled.runtime(),
    routes=(Route("GET", "/v1/status", "operations/check-status"),),
)
asgi_app = rest.asgi_app()
```

GET/HEAD routes may target only read-only Tools. Writing routes may target only
writing Tools. Unlisted refs are unreachable through REST. Neither surface
duplicates business logic or replaces your service's authorization checks.
