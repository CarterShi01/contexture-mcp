"""Bounded WP09 managed MCP gateway candidate, not an accepted wire profile.

The five SDK tools are fixed; the managed graph behind them is mutable. This
does not make the static sealed-graph wire claim. Prompt/Resource publication is
a bounded candidate. Run routing uses explicit trusted resolution; HTTP headers
require separate opt-in. Run creation remains a Host operation, not a gateway Tool.
Applications supply authenticated request context to ManagedRuntime and own its
coordinator/resources. Building or serving this adapter does not write resources
or shut them down. An optional explicit SDK lifespan remains application-owned.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from starlette.requests import Request

from ..application.managed_run_leases import RunLeaseContext
from ..application.managed_runtime import ManagedRuntime
from ..core.constants import PACKAGE_VERSION
from ..core.run_context import (
    MAX_RUN_HEADER_BYTES, RUN_CARRIERS_META_KEY, RUN_HEADER_NAME, RUN_META_KEY,
    RunCarrierError, validate_run_carrier,
)
from ..core.graph.errors import GraphError
from ..core.model.system_api import GATEWAY
from ..core.types import CompiledContext
from .managed_publications import PUBLIC_ERROR_CODES, PublicationMCPServer, publication_call

class RunContextResolver(Protocol):
    """Explicit trusted Host port, called once per tool request, never cached.

    Inspect the SDK's public context.request_context.meta for contexture/run.
    Authenticate against the app's current Host evidence and resolve an envelope
    issued by its RunLeaseService; the metadata is routing, never authority.
    Reject malformed, unknown or unsupported channels rather than returning None
    for a supplied run. Explicitly enabled HTTP uses Contexture-Run from the
    public Request headers instead; it cannot be combined with MCP metadata.
    None means an ordinary fresh request only when no selector was supplied.
    No implicit session affinity or business-argument token is provided.
    """

    async def resolve(self, context: Context) -> RunLeaseContext | None: ...


class ManagedMCPServer:
    """One explicit runtime behind a fixed, generation-neutral SDK gateway."""

    def __init__(
        self, runtime: ManagedRuntime, *, name: str = "contexture-managed",
        version: str = PACKAGE_VERSION,
        run_context_resolver: RunContextResolver | None = None,
        enable_run_header: bool = False,
        lifespan: Callable[[MCPServer], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        if type(enable_run_header) is not bool:
            raise ValueError("Explicit run-header configuration required")
        if enable_run_header and run_context_resolver is None:
            raise ValueError("Run headers require a trusted resolver")
        self._runtime = runtime
        self._resolver = run_context_resolver
        self._run_header = enable_run_header
        self._name, self._version, self._lifespan = name, version, lifespan
        self._wire: MCPServer | None = None

    async def _resolve_run(self, ctx: Context) -> RunLeaseContext | None:
        request = ctx.request_context.request
        meta = ctx.request_context.meta or {}
        try:
            supplied = validate_run_carrier(metadata=meta,
                header_values=request.headers.getlist(RUN_HEADER_NAME) if isinstance(request, Request) else (),
                alternate_header=isinstance(request, Request) and "x-contexture-run" in request.headers,
                header_enabled=self._run_header, resolver_configured=self._resolver is not None)
        except RunCarrierError as error:
            raise GraphError(error.code) from None
        if self._resolver is None:
            run = None
        else:
            run = await self._resolver.resolve(ctx)
            if ((run is not None and type(run) is not RunLeaseContext)
                    or (supplied and run is None)):
                raise GraphError("permission_denied")
        return run

    async def _publication_call(self, ctx: Context, operation: str, key: str | None,
                                arguments: dict[str, Any] | None) -> Any:
        run = await self._resolve_run(ctx)
        async with self._runtime.request(run_context=run, publication_audience=True) as request:
            if operation == "unsupported_cursor":
                raise GraphError("schema_invalid")
            return await publication_call(request, operation, key, arguments)

    async def _call(self, ctx: Context, operation: str, *args: Any) -> Any:
        try:
            run = await self._resolve_run(ctx)
            async with self._runtime.request(run_context=run) as request:
                if operation == "discover":
                    return await request.discover()
                if operation == "inspect":
                    return await request.inspect(args[0])
                if operation == "open":
                    return await request.open(args[0])
                if operation == "invoke_read_only":
                    return await request.invoke_read_only(args[0], args[1])
                return await request.invoke(args[0], args[1])
        except GraphError as error:
            # Never render messages, locations, global diagnostics or reprs.
            code = error.code if error.code in PUBLIC_ERROR_CODES else "internal_error"
            raise ToolError(f"{code}: Managed request refused") from None
        except ToolError:
            # Resolver errors are untrusted diagnostic text too.
            raise ToolError("permission_denied: Managed context refused") from None
        except Exception:
            # Prevent SDK crash logging from exposing handler/port tracebacks.
            raise ToolError("internal_error: Managed request failed") from None

    def build(self) -> MCPServer:
        """Return the same SDK object on every call; never snapshot discover roots."""
        if self._wire is not None:
            return self._wire
        wire = PublicationMCPServer(
            publication_dispatch=self._publication_call,
            name=self._name, version=self._version,
            instructions=(
                "Managed profile candidate: call contexture_discover for current roots, "
                "then contexture_open with returned ctxg:v1 references. References are "
                "generation-qualified; rediscover after stale_reference. Use "
                "contexture_invoke_read_only for read-only Tools and contexture_invoke "
                "for writes. Explicit Prompt/Resource publications are request-authorized; "
                "no prompt arguments, caching, change notifications or implicit run session."
            ),
            lifespan=self._lifespan,
        )

        async def contexture_discover(ctx: Context) -> CompiledContext:
            return await self._call(ctx, "discover")

        async def contexture_inspect(ctx: Context, refs: list[str]) -> CompiledContext:
            return await self._call(ctx, "inspect", refs)

        async def contexture_open(ctx: Context, ref: str) -> CompiledContext:
            return await self._call(ctx, "open", ref)

        async def contexture_invoke_read_only(
            ctx: Context, ref: str, arguments: dict[str, Any] | None = None,
        ) -> Any:
            return await self._call(ctx, "invoke_read_only", ref, arguments)

        async def contexture_invoke(
            ctx: Context, ref: str, arguments: dict[str, Any] | None = None,
        ) -> Any:
            return await self._call(ctx, "invoke", ref, arguments)

        implementations = (
            contexture_discover, contexture_inspect, contexture_open,
            contexture_invoke_read_only, contexture_invoke,
        )
        carrier_meta = None
        if self._resolver is not None:
            carriers = ["mcp-meta:" + RUN_META_KEY]
            if self._run_header:
                carriers.append("http-header:" + RUN_HEADER_NAME)
            carrier_meta = {RUN_CARRIERS_META_KEY: {
                "profile": "contexture.run-carriers/v1-candidate", "carriers": carriers,
                "mutually_exclusive": True, "max_http_header_bytes": MAX_RUN_HEADER_BYTES,
            }}
        for entry, implementation in zip(GATEWAY, implementations, strict=True):
            wire.add_tool(implementation, name=entry.name, description=entry.description,
                          annotations=ToolAnnotations(read_only_hint=entry.read_only),
                          meta=carrier_meta)
        self._wire = wire
        return wire


__all__ = ["ManagedMCPServer", "RUN_META_KEY", "RUN_HEADER_NAME", "RUN_CARRIERS_META_KEY",
           "RunContextResolver"]