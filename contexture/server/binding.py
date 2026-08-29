"""Deriving a tool's schema from its type hints, and running it checked.

This is the Python side of the one seam `core` deliberately leaves open. The
object model says a tool has a `Binding` with two members; this produces one
using the MCP SDK, so that a business tool's schema comes from `invoke`'s
signature and its arguments are validated before the body ever runs.

`SDKTool.from_function` needs no server, which is what lets both happen without
the tool ever appearing in `tools/list` — and business tools never do appear
there, by design.

**The name says the derivation, not the library.** A Go implementation reflects
an argument struct and its tags; a TypeScript one takes a schema object and
infers the handler's type from it. Three names, three mechanisms, one JSON on
the wire — which is the only part conformance pins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.mcpserver.tools import Tool as SDKTool

from ..core.model.tool import Tool
from ..core.types import JsonObject


@dataclass(slots=True)
class TypeHintBinding:
    """One tool, derived once from its `invoke` signature.

    The disclosed schema is stored beside the derivation rather than recomputed:
    it is not the derivation. `title` is stripped from what an agent reads, and
    doing that walk on every card of every `open` would pay for the same result
    repeatedly. Validation still runs against the SDK's own copy, which keeps
    its titles.
    """

    tool: Tool
    _derived: SDKTool = field(init=False, repr=False)
    _schema: JsonObject = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._derived = SDKTool.from_function(
            self.tool.invoke,
            name=self.tool.name,
            description=self.tool.description,
        )
        self._schema = _without_titles(self._derived.parameters)

    @property
    def schema(self) -> JsonObject:
        return self._schema

    async def call(
        self,
        arguments: dict[str, Any] | None,
        context: Any = None,
    ) -> Any:
        """Validate arguments and run the Tool body.

        Caller identity is already request-bound by ``ApplicationRuntime``.
        Keeping that concern out of this SDK-derived binding lets MCP and REST
        use the exact same validation and invocation seam.
        """

        try:
            return await self._derived.run(arguments or {}, context)
        except UnexpectedToolError as failure:
            # PermissionError is an intentional business refusal, not a crash.
            # Newer MCP SDKs redact unexpected exception text, so translate this
            # one standard refusal before the outer gateway serialises it.
            if isinstance(failure.__cause__, PermissionError):
                raise ToolError(str(failure.__cause__)) from failure.__cause__
            raise


#: JSON Schema keywords whose value is one schema.
_SUBSCHEMA = ("items", "additionalProperties", "not", "contains", "propertyNames")

#: Keywords whose value is a list of schemas.
_SUBSCHEMA_LIST = ("anyOf", "oneOf", "allOf", "prefixItems")

#: Keywords whose value maps a *name* to a schema. The names are left alone: a
#: business tool is free to take a parameter called `title`, and stripping by
#: key name alone would delete the parameter instead of its label.
_SUBSCHEMA_MAP = ("properties", "$defs", "definitions", "patternProperties")


def _without_titles(schema: JsonObject) -> JsonObject:
    """Return the schema with every `title` keyword removed.

    Pydantic derives a title from whatever Python name it saw: the model it
    built for `invoke` becomes `"title": "invokeArguments"`, and a parameter
    called `pod` becomes `"title": "Pod"`. Both reach the agent on every tool
    card, and neither tells it anything — one is a framework internal it has no
    use for, the other is a capitalised copy of the key it sits under. Across
    the bundled reference application they came to 730 characters of nothing,
    before the payload's own indentation.

    A title stated deliberately, through `Annotated[..., Field(title=...)]`,
    goes with them. That is the accepted cost: `description` is the field a
    model actually reads, it is untouched, and it is what a parameter that needs
    explaining should carry.

    Walked by keyword rather than by key name so that a parameter named `title`
    survives — see `_SUBSCHEMA_MAP`.
    """

    cleaned: JsonObject = {}
    for key, value in schema.items():
        if key == "title":
            continue
        if key in _SUBSCHEMA_MAP and isinstance(value, dict):
            cleaned[key] = {
                name: _without_titles(sub) if isinstance(sub, dict) else sub
                for name, sub in value.items()
            }
        elif key in _SUBSCHEMA and isinstance(value, dict):
            cleaned[key] = _without_titles(value)
        elif key in _SUBSCHEMA_LIST and isinstance(value, list):
            cleaned[key] = [
                _without_titles(sub) if isinstance(sub, dict) else sub
                for sub in value
            ]
        else:
            cleaned[key] = value
    return cleaned


__all__ = ["TypeHintBinding"]
