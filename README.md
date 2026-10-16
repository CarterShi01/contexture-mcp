# Contexture

[简体中文](README.zh-CN.md) · [Documentation](docs/README.md) · [Handbook](docs/handbook.md) · [Specification](spec/README.md) · [Changelog](CHANGELOG.md)

Implementations: Python (this repository) ·
[TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript) ·
[Go](https://github.com/CarterShi01/contexture-mcp-go)

**One model for application structure and agent interaction.**

Contexture (`contexture-mcp`) is an application-layer framework for building
MCP services and AI-native applications. Organize responsibilities as **Roles**,
task guidance as **Skills**, and executable actions as **Tools**. The same
declarations organize the application and generate its agent-facing interface.

![Inside a Role: guidance, Skills, Tools and child Roles. The application graph composes these responsibility boundaries recursively.](docs/media/contexture-overview.png)

**[Watch the 1:59 overview](docs/overview.md)** ·
**[Try the built-in demo](contexture/demo/README.md)** ·
**[Build your own application](#five-minute-application)**

## Why Contexture?

Exposing functions is only part of building an agent-facing application.
Developers also need to organize responsibilities, explain how capabilities
work together, and keep that guidance aligned as the application changes.

- **Compose capabilities around responsibilities.** Keep local guidance,
    procedures, typed actions, and child Roles together; reference existing
    capabilities rather than duplicate their implementation.
- **Use the same model for structure and interaction.** Contexture checks
    declared relationships and generates navigation cards, guidance, and Tool
    schemas from the compiled graph. Agents can explore relevant branches and
    load details on demand rather than receive everything upfront.
- **Build on existing services.** Tools adapt your business code; explicit
    REST routes can share the same execution and validation runtime with MCP.

The aim is to make growing applications easier to understand, extend, and
maintain—not just to reduce an initial tool listing. Contexture is a Controller
layer: it does not run an agent loop, call a model, replace business services,
or enforce a Skill's procedure.

Frameworks such as FastMCP and mcp-use simplify MCP service development.
**Contexture makes a specific architectural choice:** responsibility-based
composition and agent interaction share one application model. See
[design, alternatives, and trade-offs](docs/design.md).

### Is it a fit?

Consider Contexture when your application has several responsibilities,
reusable procedures, and capabilities that need to evolve together. A few
independent tools may be simpler to expose directly. Navigation adds calls,
and a generic gateway does not expose each business Tool as a native top-level
MCP tool. Business authorization remains with your application and host.

### Python implementation

- Python 3.11–3.14
- MCP stdio and Streamable HTTP
- Typed and marked with `py.typed`
- Apache-2.0
- Stable 1.0 public API governed by Semantic Versioning

## Install

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install contexture-mcp
contexture --version
```

Or add it to a Python project:

```bash
uv add contexture-mcp
# or: python -m pip install contexture-mcp
```

To pin this release, request `contexture-mcp==1.0.0`.

### Explore before building

The installed CLI includes a deterministic Kubernetes incident demo. It needs
no cluster or service credentials:

```bash
contexture list contexture.demo.role:KubernetesPlatform
contexture inspect --target contexture.demo.role:KubernetesPlatform kubernetes-platform/incident-response
```

These commands inspect declarations without an LLM. Follow the
[demo guide](contexture/demo/README.md) to connect an MCP host, read fixture
evidence, and distinguish disclosure from execution.

## Five-minute application

```bash
contexture new hello-context
cd hello-context
uv sync
uv run contexture check
```

The scaffold is immediately runnable; the [handbook](docs/handbook.md) walks
through its generated `hello-context-assistant` Role. The standalone example
below uses an `operations` Role to illustrate the same small authoring model;
it does not replace the generated files automatically.

```python
from contexture import Contexture, Role, Skill, Tool


class CheckStatus(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="check-status",
            description="Return the status of one service.",
            read_only=True,
        )

    async def invoke(self, service: str) -> dict[str, str]:
        return {"service": service, "status": "ready"}


class Diagnose(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="diagnose",
            description="Diagnose an unhealthy service.",
            instructions="Read status first, then explain the evidence.",
            uses=("operations/check-status",),
        )


class Operations(Role):
    def __init__(self) -> None:
        super().__init__(
            name="operations",
            description="Handle service operations.",
            instructions="Inspect before proposing a change.",
            skills=[Diagnose()],
            tools=[CheckStatus()],
        )


app = Contexture(name="service-operations", roots=(Operations,))
```

Contexture never infers public names or descriptions from class names or
docstrings. A Tool's `invoke()` type hints produce its input schema and validate
the same call, so schema and runtime cannot drift independently.

## Development loop

```bash
uv run contexture check
uv run contexture list
uv run contexture inspect hello-context-assistant/check-target
uv run contexture call hello-context-assistant/ping --input '{"target":"example.test"}'
uv run contexture serve
```

`check` validates without opening external connections. `list` shows canonical
refs. `inspect` replays what an agent will receive. `call` executes a local
read-only Tool through the production binding; writing Tools require an
explicit `--allow-write`.

These commands target the unmodified `hello-context` scaffold. Use `list` to
obtain refs for your own application rather than copying the standalone
example's `operations` refs into an unrelated project.

## One declaration vocabulary

| Concept | What you write | Meaning |
| --- | --- | --- |
| `Contexture` | one application value | Lazy composition root |
| `Role` | subclass + constructor | Responsibility and containment boundary |
| `PreProcess` / `PostProcess` | specialized Roles + constructors | Optional preparation and finishing procedures with dedicated capabilities |
| `Skill` | subclass + constructor | Procedure the model follows |
| `Tool` | subclass + typed `invoke()` | Deterministic code Contexture executes |
| `Prompt` | subclass + constructor | User-triggered entrance to an existing node |
| `Resource` | subclass + constructor | Host-readable URI backed by a read-only Tool |
| `Channels` | optional subclass | Shared external dependencies and lifecycle |

Role, Skill, and Tool form the graph. Prompt and Resource provide another
protocol entrance to a ref the graph already owns. Use `prompt_roots` for
complete trees that only the user-controlled Prompt plane may enter.

The stable 1.0 API lets a Role hold optional `pre_process` and
`post_process` Roles. ACTIVE composes fixed, recognizable framework instruction
blocks around unchanged business `Role.instructions`, naming the actual refs to
open before starting or finishing. Procedures stay hidden until opened; opening
executes nothing and never bypasses approval. With both fields absent, existing
output and obligations are unchanged. Applications may also use
`binding_instruction` to mark their own non-enforceable hard rules under their
own authority. See the [authoring example](docs/handbook.md#optional-process-members-contexture-10)
and [ADR 023](docs/adr/023-process-members-and-instruction-emphasis.md).

## Progressive disclosure

The standard runtime MCP server exposes five fixed model-controlled tools:

```text
contexture_discover
contexture_inspect
contexture_open
contexture_invoke_read_only
contexture_invoke
```

`discover` returns root cards. `inspect` compares a shortlist through one level
of pure routing cards without activating instructions or disclosing Tool
schemas. Opening a Role returns its instructions and one
level of child Role, Skill, and Tool cards. A Tool card carries the ref, input
schema, and read-only classification needed to invoke it. Business Tools never
inflate MCP's top-level tool list.

The two invoke doors let a host apply approval policy from the visible MCP
`readOnlyHint`. Calling through the wrong door is refused. Disclosure helps a
model decide what exists; it is not authorization.

The optional disclosure-only server has discovery, inspection, and opening
without invocation; see the [handbook](docs/handbook.md).

## Connect an MCP host

After local checks pass, let the host own the stdio process:

```bash
claude mcp add --scope project hello-context -- uv run contexture serve
codex mcp add                 hello-context -- uv run contexture serve
```

For Streamable HTTP:

```bash
uv run contexture serve --transport streamable-http --port 8080
```

Non-loopback HTTP requires an explicit authentication or anonymous-access
decision plus allowed hosts/origins. Configure these with `ContextureOptions`.
See the [handbook](docs/handbook.md).

An HTTP deployment can attenuate a request to complete subtrees with the
`Contexture-Select` header and `HeaderSurfaceSelector`. Exact refs such as
`team/notebook-editor` select that complete subtree; `team/*` selects each
direct member subtree, and `*` never crosses `/`. The legacy
`Contexture-Roots` and `HeaderRootSelector` names remain compatible. An
application-owned ceiling derived from the verified `Principal` can restrict
the selection further. Selection is a surface boundary, not permission policy.

```python
from contexture.server import HeaderSurfaceSelector, compile_application

server = compile_application(app).server(
    surface_selector=HeaderSurfaceSelector(),
)
```

## Human-facing REST routes

An agent benefits from navigation; a dashboard has already chosen its pages
and buttons. Expose an explicit Tool allowlist:

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
writing Tools. Unlisted refs are unreachable through REST.

## Learn more

- [Documentation map](docs/README.md): concepts, tutorials, reference, and history.
- [Design and trade-offs](docs/design.md): why one model, and when not to use it.
- [Illustrated overview and transcript](docs/overview.md): the implemented interaction model.
- [Roadmap](docs/roadmap.md): planned visualization and capability-level evaluation.

## Public API

Use `contexture` for declarations and `contexture.server` for advanced hosting.
Their export sets are regression-tested. `contexture.core` and concrete server
submodules are implementation details even though Python can import them.

## Work on Contexture

```bash
git clone https://github.com/CarterShi01/contexture-mcp.git
cd contexture-mcp
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests
uv build
uv run --extra dev twine check --strict dist/*
```

Read the [contribution guide](CONTRIBUTING.md) before changing a contract, the
[handbook](docs/handbook.md) for application development, and the
[language-neutral specification](spec/README.md) when implementing another
binding. Report vulnerabilities according to [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
