# Authoring an application

[简体中文](authoring.zh-CN.md) · [Documentation](../README.md) · [Tutorial](../handbook.md)

Use this guide as a compact reference for declarations. For a runnable project
from installation through your first change, follow the [handbook](../handbook.md).

## A complete declaration

This standalone example combines a responsibility, a reusable procedure, and a
typed action. It uses `operations` refs; the CLI scaffold uses
`hello-context-assistant` refs instead. It does not replace generated files automatically.

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

## Declaration vocabulary

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

The stable 1.0 API lets a Role hold optional `pre_process` and `post_process`
Roles. ACTIVE composes recognizable framework instruction blocks around
unchanged business `Role.instructions`, naming refs to open before starting
or finishing. These are separately disclosed procedures, not automatically
executed callbacks. `binding_instruction` marks application-owned, non-enforceable
rules. See the [process-member example](../handbook.md#optional-process-members-contexture-10)
and [ADR 023](../adr/023-process-members-and-instruction-emphasis.md).

## Development loop

Use `contexture check` to validate, `contexture list` to find canonical refs,
`contexture inspect` to see generated disclosure, and `contexture call` to
execute one local Tool. The CLI's inspection command can replay opening a ref;
it is different from the routing-only MCP tool `contexture_inspect`.

Start with the [generated project's commands](../handbook.md#4-complete-the-local-development-loop),
then [add capabilities in the right place](../handbook.md#6-add-capabilities-in-the-right-place).
For external dependencies, use the [Channels guide](../handbook.md#7-connect-external-systems-only-when-needed).

## Public API boundaries

Use `contexture` for declarations and `contexture.server` for advanced hosting.
Their export sets are regression-tested. `contexture.core` and concrete server
submodules are implementation details even though Python can import them.
The [specification](../../spec/README.md) defines cross-language contracts;
[hosting](hosting.md) explains the MCP and REST surfaces.
