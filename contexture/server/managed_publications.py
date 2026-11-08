"""Bounded WP09 publication candidate, not an accepted normative wire profile.

Dynamic exact publication mappings live only inside one runtime request. No
static manager registration, path fallback, prompt arguments, cache, paging,
subscriptions or list-change notifications are implemented. Oversize replies
fail atomically. SDK integration targets the installed official 2.x API.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    GetPromptResult, ListPromptsResult, ListResourcesResult, Prompt,
    PromptMessage, ReadResourceResult, Resource, TextContent, TextResourceContents,
)

from ..application.managed_runtime import ManagedRequest
from ..core.graph.errors import GraphError

MAX_PUBLICATIONS = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PUBLIC_ERROR_CODES = frozenset({"permission_denied", "unsupported_context", "schema_invalid",
    "stale_reference", "too_large", "wrong_door", "binding_contract_invalid",
    "generation_unavailable", "resource_busy", "limit_exceeded"})


def bounded(value: Any) -> Any:
    """Refuse the complete reply, never silently truncate a dynamic list."""
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > MAX_RESPONSE_BYTES:
            raise GraphError("too_large")
    return value


async def publication_call(request: ManagedRequest, operation: str, key: str | None = None,
                           arguments: dict[str, Any] | None = None) -> Any:
    """Resolve public selectors to stable publication IDs on the pinned handle."""
    prompt = operation in ("list_prompts", "get_prompt")
    inventory = await (request.get_prompt() if prompt else request.read_resource())
    if operation.startswith("list_"):
        if len(inventory) > MAX_PUBLICATIONS:
            raise GraphError("too_large")
        rows = list(inventory.values())
        bounded(rows)
        if prompt:
            result = ListPromptsResult(prompts=[Prompt(name=p["name"],
                description=p["description"], arguments=[], _meta={"ref": p["ref"]}) for p in rows],
                ttl_ms=0, cache_scope="private")
        else:
            result = ListResourcesResult(resources=[Resource(name=p["name"], uri=p["uri"],
                mime_type=p["mime_type"], _meta={"ref": p["ref"]}) for p in rows],
                ttl_ms=0, cache_scope="private")
        bounded(result.model_dump(mode="json", by_alias=True))
        return result
    if arguments:
        raise GraphError("schema_invalid")
    field = "name" if prompt else "uri"
    publication_id = next((pid for pid, row in inventory.items() if row[field] == key), None)
    if publication_id is None:
        raise GraphError("permission_denied")
    row = inventory[publication_id]
    if prompt:
        content = await request.get_prompt(publication_id)
        result = GetPromptResult(description=row["description"], messages=[PromptMessage(
            role="user", content=TextContent(text=json.dumps(bounded(content), ensure_ascii=False)))])
    else:
        content = await request.read_resource(publication_id, expected_uri=key)
        result = ReadResourceResult(contents=[TextResourceContents(uri=row["uri"],
            mime_type=row["mime_type"], text=json.dumps(bounded(content["value"]), ensure_ascii=False))])
    bounded(result.model_dump(mode="json", by_alias=True))
    return result


class PublicationMCPServer(MCPServer):
    """SDK handler overrides retain request context even for dynamic lists.

    The installed SDK has no public removal API. Its default subscriptions
    handler is removed at construction (before serving) solely to avoid falsely
    advertising listChanged/subscribe under modern capability derivation.
    No runtime internals or handler managers are consulted by publications.
    """

    def __init__(self, *, publication_dispatch: Callable[..., Awaitable[Any]], **kwargs: Any) -> None:
        self._publication_dispatch = publication_dispatch
        super().__init__(**kwargs)
        self._lowlevel_server._request_handlers.pop("subscriptions/listen", None)

    async def _publication(self, ctx: ServerRequestContext[Any], operation: str,
                           key: str | None = None, arguments: dict[str, Any] | None = None) -> Any:
        try:
            return await self._publication_dispatch(Context(request_context=ctx, mcp_server=self),
                                                    operation, key, arguments)
        except GraphError as error:
            code = error.code if error.code in PUBLIC_ERROR_CODES else "internal_error"
            raise MCPError(code=-32602, message=f"{code}: Managed request refused") from None
        except Exception:
            raise MCPError(code=-32603, message="internal_error: Managed request failed") from None

    async def _handle_list_prompts(self, ctx: Any, params: Any) -> Any:
        if params is not None and params.cursor is not None:
            # Still enter the request for authorization; no paging contract.
            return await self._publication(ctx, "unsupported_cursor")
        return await self._publication(ctx, "list_prompts")

    async def _handle_list_resources(self, ctx: Any, params: Any) -> Any:
        if params is not None and params.cursor is not None:
            return await self._publication(ctx, "unsupported_cursor")
        return await self._publication(ctx, "list_resources")

    async def _handle_get_prompt(self, ctx: Any, params: Any) -> Any:
        return await self._publication(ctx, "get_prompt", params.name, params.arguments)

    async def _handle_read_resource(self, ctx: Any, params: Any) -> Any:
        return await self._publication(ctx, "read_resource", params.uri)