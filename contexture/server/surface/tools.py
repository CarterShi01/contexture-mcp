"""The four entry points, and the reason a business capability is not among them.

MCP tool lists are flat and, since the 2026-07-28 revision, stateless: a server
may not vary them per connection or as a consequence of an earlier call. So a
capability that is registered is one every session pays for, forever, whatever
the user asked. The only way a tool becomes deferrable is for it not to be on
the surface at all — its name, description and schema travel inside a payload
and arrive when the role holding it is opened.

What is on the surface is four tools, whatever the declaration contains:

    contexture_discover              the root roles, one level
    contexture_open                  one node's detail, plus its members' cards
    contexture_invoke_read_only      run a tool that leaves the world unchanged
    contexture_invoke                run a tool that does not

**`read_only` is which door, not which argument.** A host cannot see a business
tool any more, so it cannot be told per tool whether to ask a human first. It
can see which of the two doors was used, and each door carries the matching
`readOnlyHint`. A model may pick the wrong one — and picking it gets the call
refused rather than executed, which is the same protection as never letting the
classification be an argument, relocated to where the host can still act on it.

Nothing is checked in this door's constructor because a business declares
nothing on it. It is a class all the same, so that a surface reads as three
doors rather than two doors and an exception.
"""

from __future__ import annotations

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from ...core.model.system_api import DisclosureAPI, ExecutionAPI, GATEWAY
from ...core.types import CompiledContext
from ..identity import principal_of
from . import translated


class Tools:
    """The fixed four. Nothing a declaration says changes them."""

    __slots__ = ("_disclosure", "_execution")

    def __init__(self, disclosure: DisclosureAPI, execution: ExecutionAPI) -> None:
        self._disclosure = disclosure
        self._execution = execution

    def install(self, wire: MCPServer) -> None:
        disclosure = self._disclosure
        execution = self._execution

        # Four wrappers holding no rules of their own. Each exists for one thing
        # the kernel cannot have: an SDK `Context` to thread through, and a
        # signature for the SDK to derive this entry point's own schema from.
        async def contexture_discover() -> CompiledContext:
            with translated():
                return await disclosure.discover()

        async def contexture_open(ref: str) -> CompiledContext:
            with translated():
                return await disclosure.open(ref)

        async def contexture_invoke_read_only(
            ctx: Context,
            ref: str,
            arguments: dict[str, Any] | None = None,
        ) -> Any:
            with translated():
                return await execution.invoke_read_only(
                    ref, arguments, context=ctx,
                    principal=principal_of(get_access_token()),
                )

        async def contexture_invoke(
            ctx: Context,
            ref: str,
            arguments: dict[str, Any] | None = None,
        ) -> Any:
            with translated():
                return await execution.invoke(
                    ref, arguments, context=ctx,
                    principal=principal_of(get_access_token()),
                )

        implementations = dict(
            zip(
                (entry.name for entry in GATEWAY),
                (
                    contexture_discover,
                    contexture_open,
                    contexture_invoke_read_only,
                    contexture_invoke,
                ),
            )
        )

        # Registered from the kernel's own list rather than four call sites, so
        # "the surface is exactly these four, described exactly this way" is a
        # fact about one tuple instead of an agreement between eight places.
        for entry in GATEWAY:
            wire.add_tool(
                implementations[entry.name],
                name=entry.name,
                description=entry.description,
                annotations=ToolAnnotations(read_only_hint=entry.read_only),
            )


__all__ = ["Tools"]
