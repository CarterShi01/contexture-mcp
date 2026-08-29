"""The two progressive-disclosure tools installed without execution."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from ...core.model.system_api import DISCLOSURE_GATEWAY, DisclosureAPI
from ...core.types import CompiledContext
from . import translated


class NavigationTools:
    """Install only ``discover`` and ``open`` over a DisclosureAPI."""

    __slots__ = ("_api",)

    def __init__(self, api: DisclosureAPI) -> None:
        self._api = api

    def install(self, wire: MCPServer) -> None:
        api = self._api

        async def contexture_discover() -> CompiledContext:
            with translated():
                return await api.discover()

        async def contexture_open(ref: str) -> CompiledContext:
            with translated():
                return await api.open(ref)

        implementations = (contexture_discover, contexture_open)
        for entry, implementation in zip(DISCLOSURE_GATEWAY, implementations):
            wire.add_tool(
                implementation,
                name=entry.name,
                description=entry.description,
                annotations=ToolAnnotations(read_only_hint=True),
            )


__all__ = ["NavigationTools"]
