"""The three progressive-disclosure tools installed without execution."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from ...core.model.system_api import DISCLOSURE_GATEWAY, DisclosureAPI
from ...core.types import CompiledContext
from ...core.presentation import GatewayPresentation
from . import translated


class NavigationTools:
    """Install only ``discover``, ``inspect``, and ``open`` over a DisclosureAPI."""

    __slots__ = ("_api", "_presentation")

    def __init__(
        self, api: DisclosureAPI, presentation: GatewayPresentation | None
    ) -> None:
        self._api = api
        self._presentation = presentation

    def install(self, wire: MCPServer) -> None:
        api = self._api

        async def contexture_discover() -> CompiledContext:
            with translated():
                return await api.discover()

        async def contexture_inspect(refs: list[str]) -> CompiledContext:
            with translated():
                return await api.inspect(refs)

        async def contexture_open(ref: str) -> CompiledContext:
            with translated():
                return await api.open(ref)

        implementations = (contexture_discover, contexture_inspect, contexture_open)
        for entry, implementation in zip(DISCLOSURE_GATEWAY, implementations):
            title = (
                self._presentation.title_for(entry.name)
                if self._presentation is not None
                else None
            )
            wire.add_tool(
                implementation,
                name=entry.name,
                title=title,
                description=entry.description,
                annotations=ToolAnnotations(title=title, read_only_hint=True),
            )


__all__ = ["NavigationTools"]
