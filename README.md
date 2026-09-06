# Contexture

[简体中文](README.zh-CN.md) · [Handbook](docs/handbook.md) · [Specification](spec/README.md) · [Changelog](CHANGELOG.md)

Implementations: Python (this repository) ·
[TypeScript](https://github.com/CarterShi01/contexture-mcp-typescript) ·
[Go](https://github.com/CarterShi01/contexture-mcp-go)

Contexture is a Python framework for exposing a large application capability
graph to agents without placing every tool and instruction in the model's
context at once. You declare Roles, Skills, and Tools; Contexture compiles an
immutable graph and serves a small, fixed MCP gateway that discloses only the
branch an agent chooses.

The same application runtime can back explicit REST routes for human
interfaces. Contexture is a Controller layer: it does not contain an agent
loop, call a model, or replace your business services.

- Python 3.11–3.14
- MCP stdio and Streamable HTTP
- Typed and marked with `py.typed`
- Apache-2.0
- Beta: public surfaces are snapshotted, but may still change before 1.0

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

For a release candidate, request the exact version, such as
`contexture-mcp==0.12.0rc1`.

## Five-minute application

```bash
contexture new hello-context
cd hello-context
uv sync
uv run contexture check
```

The authoring model is deliberately small:

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
uv run contexture inspect operations/diagnose
uv run contexture call operations/check-status --input '{"service":"api"}'
uv run contexture serve
```

`check` validates without opening external connections. `list` shows canonical
refs. `inspect` replays what an agent will receive. `call` executes a local
read-only Tool through the production binding; writing Tools require an
explicit `--allow-write`.

## One declaration vocabulary

| Concept | What you write | Meaning |
| --- | --- | --- |
| `Contexture` | one application value | Lazy composition root |
| `Role` | subclass + constructor | Responsibility and containment boundary |
| `Skill` | subclass + constructor | Procedure the model follows |
| `Tool` | subclass + typed `invoke()` | Deterministic code Contexture executes |
| `Prompt` | subclass + constructor | User-triggered entrance to an existing node |
| `Resource` | subclass + constructor | Host-readable URI backed by a read-only Tool |
| `Channels` | optional subclass | Shared external dependencies and lifecycle |

Role, Skill, and Tool form the graph. Prompt and Resource provide another
protocol entrance to a ref the graph already owns. Use `prompt_roots` for
complete trees that only the user-controlled Prompt plane may enter.

## Progressive disclosure

MCP hosts always see four fixed model-controlled tools:

```text
contexture_discover
contexture_open
contexture_invoke_read_only
contexture_invoke
```

`discover` returns root cards. Opening a Role returns its instructions and one
level of child Role, Skill, and Tool cards. A Tool card carries the ref, input
schema, and read-only classification needed to invoke it. Business Tools never
inflate MCP's top-level tool list.

The two invoke doors let a host apply approval policy from the visible MCP
`readOnlyHint`. Calling through the wrong door is refused. Disclosure helps a
model decide what exists; it is not authorization.

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

An HTTP deployment can attenuate a request to complete root trees with the
`Contexture-Roots` header and `HeaderRootSelector`. An application-owned
ceiling derived from the verified `Principal` can restrict it further. Root
selection is a surface boundary, not permission policy.

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
