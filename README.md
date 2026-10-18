# Contexture

<!-- markdownlint-configure-file {"MD033": {"allowed_elements": ["video", "a", "details", "summary", "strong"]}} -->

**One model for application structure and agent interaction.**

Contexture (`contexture-mcp`) is an application-layer framework for building
MCP services and AI-native applications. Compose responsibilities, guidance,
and typed actions into a capability graph; the same declarations organize
your application and generate the interface agents navigate.

[Documentation](docs/README.md) · [Quickstart](#quickstart) ·
[Demo](contexture/demo/README.md) · [Changelog](CHANGELOG.md) · [简体中文](README.zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/contexture-mcp)](https://pypi.org/project/contexture-mcp/)
[![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue)](pyproject.toml)
[![Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

<video src="https://raw.githubusercontent.com/CarterShi01/contexture-mcp/894cb4f1032f7411fb3faa791a3b9c3782a58a71/docs/media/contexture-overview.mp4#t=0.1" controls width="960">Contexture overview: application structure and agent interaction.</video>

*1:59 · Implemented architecture, illustrated. [Transcript and subtitles](docs/overview.md)
· [Download video](docs/media/contexture-overview.mp4?raw=1).*

## Why Contexture?

Growing applications need more than callable tools: they need responsibility
boundaries, reusable guidance, and a structure developers can maintain.

- **Compose by responsibility.** Roles keep local guidance, Skills, typed Tools,
  and child Roles together. Reuse capabilities through explicit references.
- **Declare once, disclose progressively.** Checked declarations drive the
  capability graph, navigation, guidance, and schemas. Agents open the details
  they need, then invoke Tools explicitly.
- **Keep your business services.** Adapt existing code instead of duplicating
  it. MCP and explicit REST routes share execution and validation.

Contexture is a Controller layer, not an agent loop or workflow engine.
It is useful when responsibilities and procedures need to evolve together;
a handful of independent tools may be simpler to expose directly.
[Read the design and trade-offs →](docs/design.md)

<a id="install"></a>
<a id="five-minute-application"></a>

## Quickstart

Requires Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/).
Create a local application and call its first Tool—no model or credentials needed:

```bash
uv tool install contexture-mcp
contexture new hello-context
cd hello-context
uv sync
uv run contexture check
uv run contexture call hello-context-assistant/ping --input '{"target":"example.test"}'
```

```text
example.test: reachable (placeholder)
```

Next, follow the [tutorial](docs/handbook.md) to inspect and extend the
generated app, or try the [Kubernetes demo](contexture/demo/README.md).
Already have a project? Add the package with `uv add contexture-mcp`.
To pin the current 1.0 release, use `contexture-mcp==1.0.0`.

<a id="connect-an-mcp-host"></a>
<details>
<summary><strong>Connect Claude Code or Codex</strong></summary>

From `hello-context`, choose one command. The host starts the stdio server:

```bash
# Claude Code: project-scoped registration
claude mcp add --scope project hello-context -- uv run contexture serve

# Or Codex: updates user configuration
codex mcp add hello-context -- uv run contexture serve
```

Start the chosen host in this project and verify its MCP connection.
See [hosting](docs/guides/hosting.md) for configuration scope, HTTP, and REST.

</details>

<a id="one-declaration-vocabulary"></a>

## The application model

The scaffold is already a small capability graph:

```text
hello-context-assistant       Role: a responsibility and local guidance
├── check-target             Skill: a procedure referencing ping
└── ping                     Tool: typed executable code
```

`Role` contains capabilities and child Roles; `Skill` describes a procedure;
`Tool` executes business code. Explicit `uses` references connect capabilities.
Typed Tool signatures generate schemas and validate calls.

**[See a complete Python declaration →](docs/guides/authoring.md)**

<a id="progressive-disclosure"></a>

The runtime exposes five MCP gateways for discovery, inspection, opening, and
read/write invocation. Opening reveals guidance and schemas; invocation runs
the Tool. Business authorization stays with the application and host.
[How disclosure works →](docs/guides/hosting.md#gateway-and-disclosure)

<a id="learn-more"></a>

## Documentation

| I want to… | Read |
| --- | --- |
| Build and extend my first application | [Tutorial](docs/handbook.md) |
| Compose Roles, Skills, and Tools | [Authoring guide](docs/guides/authoring.md) |
| Connect a host or serve MCP / REST | [Hosting guide](docs/guides/hosting.md) |
| Understand the design and alternatives | [Design and trade-offs](docs/design.md) |
| Explore a working example | [Incident demo](contexture/demo/README.md) |
| See planned improvements | [Roadmap](docs/roadmap.md) |

The [documentation index](docs/README.md) separates tutorials, task guides,
explanations, and reference material. Python is this repository's implementation;
also see [TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript) and
[Go](https://github.com/CarterShi01/contexture-mcp-go).

<a id="development-loop"></a>
<a id="human-facing-rest-routes"></a>
<a id="public-api"></a>

Looking for a previous README section? The [development loop](docs/guides/authoring.md#development-loop),
[REST example](docs/guides/hosting.md#human-facing-rest-routes), and
[API boundaries](docs/guides/authoring.md#public-api-boundaries) now live in the guides.

<a id="work-on-contexture"></a>

## Contributing

Bug reports, documentation improvements, and focused contributions are welcome.
Start with [CONTRIBUTING.md](CONTRIBUTING.md); discuss changes in
[Issues](https://github.com/CarterShi01/contexture-mcp/issues).
Report vulnerabilities privately through [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
