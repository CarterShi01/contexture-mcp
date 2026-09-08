"""What this server exposes on MCP's **tool** primitive.

Tools are the model-controlled primitive: the model decides when one is called.
Everything in the object model reaches an agent through this one primitive, and
it reaches it through a fixed system API rather than by being listed.

**This is the one plane a business may not extend, and that is the design.**
On the other two, a declaration adds entries: a `Prompt` names a node a person
may trigger, a `Resource` names one a host may take up. Here it adds none. Tool
lists are flat and, since the 2026-07-28 revision, stateless — a server may not
vary them per connection or as a consequence of an earlier call — so a
capability that is registered is one every session pays for, forever, whatever
the user asked. The only way a capability becomes deferrable is for it not to be
listed at all: its name, description and schema travel inside a payload and
arrive when the role holding it is opened.

So what occupies this plane is the framework's own five entry points, and this
module declares **which** they are and nothing else. Their descriptions and
their behaviour live together in `core.model.system_api`, because since ADR 014
navigation is part of the kernel. The names arrive from the shared ground, so
that naming them here and implementing them there cannot drift into two lists.

Declaration only. Nothing here imports the SDK, builds an annotation, or knows
how a call is dispatched; `server` does all of that.
"""

from __future__ import annotations

from ..constants import (
    DISCOVER_TOOL,
    INSPECT_TOOL,
    INVOKE_READ_ONLY_TOOL,
    INVOKE_TOOL,
    OPEN_TOOL,
)

from typing import ClassVar, final


@final
class ToolPlane:
    """The tool plane, as a type a business cannot extend.

    The other two planes are classes a declaration subclasses: a `Prompt` names
    a node a person may trigger, a `Resource` one a host may take up. This one
    ships no such base, and that absence *is* the rule — but an absence is hard
    to see, and a server signature that simply omitted a `tools=` parameter
    would read as an oversight rather than a decision.

    So the plane is a value with exactly one instance, `TOOLS`, and a type that
    refuses to be subclassed. `ContextureServer(index, tools=TOOLS, prompts=…,
    resources=…)` then reads as a table: three planes, and the one whose
    argument you cannot vary is the one you cannot extend. The type is the rule,
    where before it was a paragraph.
    """

    #: Every name on this plane, in registration order. Five, whatever the
    #: declaration contains — business capabilities travel inside payloads.
    names: ClassVar[tuple[str, ...]] = (
        DISCOVER_TOOL,
        INSPECT_TOOL,
        OPEN_TOOL,
        INVOKE_READ_ONLY_TOOL,
        INVOKE_TOOL,
    )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError(
            "The tool plane is not extensible: a business capability reaches an "
            "agent inside a payload, not by being registered here. See this "
            "module's docstring."
        )


#: The one instance. What a server is handed for its tool plane, and the only
#: value that argument can take.
TOOLS = ToolPlane()

__all__ = [
    "DISCOVER_TOOL",
    "INSPECT_TOOL",
    "INVOKE_READ_ONLY_TOOL",
    "INVOKE_TOOL",
    "OPEN_TOOL",
    "TOOLS",
    "ToolPlane",
]
