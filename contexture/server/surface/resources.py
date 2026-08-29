"""What a host may take up on its own: documents, at addresses of their own.

MCP's resource primitive is the *application-controlled* one — a host decides
when to read one, without asking a model and without a person picking it. Each
entry names a node the tree already holds, so a procedure can cite a document by
the name the document gives itself and a model can reach the same bytes by
navigating to it. One capability, two addresses; not two declarations that can
disagree.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.resources import FunctionResource

from ...core.errors import ModelValidationError
from ...core.mcp_interface.resource import Resource
from ...core.model.disclosure import Disclosure
from ...core.model.system_api import ExecutionAPI
from . import published_name, translated
from ..identity import principal_of


class Resources:
    """The declared documents, checked against what they name."""

    __slots__ = ("_execution", "_entries")

    def __init__(
        self,
        tree: Disclosure,
        execution: ExecutionAPI,
        entries: tuple[Resource, ...],
    ) -> None:
        """Refuse a resource that does not name content already sitting there.

        A resource is *fetched*, not computed: two reads return the same bytes
        until the document itself changes. That shape is exactly a read-only
        tool with no arguments, and both halves are checked — a tool with
        parameters has no answer to give when a host reads it with none, and a
        writing tool run by a host that thinks it is fetching a document is the
        wrong door with nobody at it.

        Names and URIs are checked here too, for the reason `Prompts` states:
        these are flat addresses in a menu, and a ref is what tells two
        same-named nodes apart.
        """

        names: dict[str, str] = {}
        uris: dict[str, str] = {}
        for entry in entries:
            name = published_name(entry)
            if name in names:
                raise ModelValidationError(
                    f"{names[name]!r} and {entry.opens!r} are both exposed as "
                    f"the resource {name!r}. A ref tells them apart and a name "
                    "in a menu cannot; rename one."
                )
            names[name] = entry.opens
            if entry.uri in uris:
                raise ModelValidationError(
                    f"{uris[entry.uri]!r} and {entry.opens!r} are both "
                    f"published at {entry.uri!r}. One address names one thing."
                )
            uris[entry.uri] = entry.opens

            tool = tree.tool(entry.opens)
            if not tool.read_only:
                raise ModelValidationError(
                    f"Resource {entry.uri!r} names {entry.opens!r}, which is "
                    "not read-only. Reading a resource must leave the world "
                    "unchanged."
                )
            if tree.schema_of(tool).get("properties"):
                raise ModelValidationError(
                    f"Resource {entry.uri!r} names {entry.opens!r}, which takes "
                    "arguments. A host reads a resource with none, so what it "
                    "names has to answer with none."
                )
        self._execution = execution
        self._entries = entries

    def install(self, wire: MCPServer) -> None:
        execution = self._execution
        for entry in self._entries:
            wire.add_resource(
                FunctionResource.from_function(
                    _reader(execution, entry.opens),
                    uri=entry.uri,
                    name=published_name(entry),
                    description=entry.description,
                    mime_type=entry.mime_type,
                )
            )


def _reader(api: ExecutionAPI, ref: str) -> Callable[[], Awaitable[Any]]:
    """Build the function a host calls when it reads this resource.

    Resolved per call rather than captured, for the same reason a command's text
    is assembled per call: one node reached two ways must not be able to answer
    two different things.

    Through the kernel, like the other two doors. Until ADR 016 this reached the
    tool directly, which left one of the three paths into a capability without
    argument validation and without a caller's identity in reach of the code
    that runs.
    """

    async def read() -> Any:
        with translated():
            return await api.read_for_host(
                ref, principal=principal_of(get_access_token())
            )

    return read


__all__ = ["Resources"]
