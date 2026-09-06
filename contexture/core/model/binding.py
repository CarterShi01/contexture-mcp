"""What one tool needs in order to be described and to be run.

`core` cannot derive a JSON Schema — it does not import the SDK, and the three
implementations of this framework do not agree on how a schema is produced
anyway: Python reads `invoke`'s signature, Go reflects an argument struct,
TypeScript declares a schema object. What conformance pins is the schema that
reaches the wire, never how it was derived.

So `core` states the shape and fills it with the honest minimum. A `Binding` is
**one tool's** two derived facts:

    schema      what a card carries, so an agent knows how to call it
    call        run it with those arguments checked

**They are two views of one derivation, and that is why they are one object.**
A schema written on a card by one thing and a check applied to a call by
another is the worst kind of drift: an agent calls exactly what it was told to
call and is refused. Here neither can move without the other.

**One per tool, made once.** A binding is derived when the forest is compiled
and stored beside the address it belongs to. What this replaces was a single
process-wide object holding a dictionary keyed by `id(tool)` — a per-tool fact
implemented as a global table, which then needed a paragraph explaining when an
`id` may be reused. An address is stable and unique, so the paragraph goes.

The strategy that produces one is named where the SDK is:

    index = Index.of(manager, bind=TypeHintBinding)     # Python
    index = Index.of(manager, bind=StructTagBinding)    # Go, in spirit
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..types import JsonObject
from .tool import Tool


@runtime_checkable
class Binding(Protocol):
    """One tool's schema, and the way to run it. Two members, no more."""

    @property
    def schema(self) -> JsonObject:
        """The input schema an agent needs in order to call this tool."""
        ...

    async def call(
        self,
        arguments: dict[str, Any] | None,
        context: Any,
    ) -> Any:
        """Run the tool with these arguments.

        `context` is whatever per-request handle the layer above needs threaded
        through; nothing in `core` ever looks inside it.
        """
        ...


@dataclass(frozen=True, slots=True)
class PlainBinding:
    """No schema, no validation — what a caller gets with no wire in the room.

    A tree compiled without a binding strategy is a tree nobody is serving: a
    test, `contexture list`, or anything driving the object model directly.
    Answering with an empty schema and a direct call is more useful there than
    obliging every such caller to supply a strategy it does not need.

    It is also the floor the two seams it replaces used to hold separately —
    `_no_schema` on the tree and `_plain_invoke` in the kernel. One default is
    one place for a reader to look.
    """

    tool: Tool

    @property
    def schema(self) -> JsonObject:
        return {}

    async def call(
        self,
        arguments: dict[str, Any] | None,
        context: Any = None,
    ) -> Any:
        invoke = getattr(self.tool, "invoke")
        return await invoke(**(arguments or {}))


__all__ = ["Binding", "PlainBinding"]
