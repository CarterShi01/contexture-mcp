"""Bounded WP09 candidate REST Host; not a normative wire profile.

Only explicit graph RoutePublications are served, using one runtime request pin.
The Host owns runtime.contexts authentication (for example trusted middleware);
payloads cannot supply identity, selection, authority or run contexts. This
adapter owns neither runtime startup/shutdown nor any management endpoints.

Candidate parsing follows the legacy adapter's GET/HEAD query contract: strings,
or lists of strings for repeated keys, never inferred JSON/coercion. Read bodies
and write queries are refused rather than silently ignored. Write methods accept
an explicit JSON object (empty body means {}); media type may be absent or JSON.
Run headers require explicit opt-in and a trusted resolver returning an actual
run-pinned request. There is no HEAD-to-GET fallback, path templating, URI
normalization, paging, watch, discovery, or management route. Mount on a dedicated Host prefix;
do not place this catch-all ahead of unrelated or management applications.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
import json
from typing import Protocol
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from contexture.core.graph.canonical import decode_json, thaw_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import RoutePublication
from contexture.core.run_context import RUN_HEADER_NAME, RunCarrierError, validate_run_carrier

MAX_BODY_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT")


class RouteRequest(Protocol):
    """Trusted public port; implementations must fence pin and current policy."""

    @property
    def generation_uuid(self) -> str: ...

    async def list_routes(self) -> tuple[RoutePublication, ...]: ...

    async def invoke_route(self, publication_id: str, arguments: object = None, *,
                           expected_method: str, expected_path: str) -> object: ...


class RouteRuntime(Protocol):
    def request(self, *, publication_audience: bool = False
                ) -> AbstractAsyncContextManager[RouteRequest]: ...


class PinnedRouteRequest(RouteRequest, Protocol):
    @property
    def is_run_pinned(self) -> bool: ...


class RouteRunResolver(Protocol):
    """Trusted Host mapping to the same runtime's validated run request scope.

    Unknown selectors return None or refuse. Never construct a fresh request
    as a fallback; the adapter also checks the explicit lifetime marker.
    Identity and current policy remain the runtime's independent responsibility.
    """

    async def resolve(self, request: Request) -> AbstractAsyncContextManager[PinnedRouteRequest] | None: ...


class _BadRequest(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def _encoded(value: object) -> bytes:
    # Detach and validate: never default=str or serialize arbitrary native data.
    detached = thaw_json(value)
    chunks = bytearray()
    for text in json.JSONEncoder(ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).iterencode(detached):
        chunk = text.encode("utf-8")
        if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
            raise _BadRequest(500, "response-too-large")
        chunks.extend(chunk)
    return bytes(chunks)


def _response(body: bytes, status: int, *, head: bool, generation: str | None = None) -> Response:
    headers = {"cache-control": "no-store", "content-length": str(len(body))}
    if generation is not None:
        headers["Contexture-Generation"] = generation
    return Response(b"" if head else body, status_code=status, media_type="application/json",
                    headers=headers)


class ManagedRestSurface:
    """Starlette ASGI adapter over public request/list_routes/invoke_route only.

    No static route table is retained. Every HTTP call re-enters the runtime,
    maps metadata and invokes within that SAME pinned request. Authentication
    must already be established by the trusted Host's runtime contexts port.
    """

    def __init__(self, runtime: RouteRuntime, *, run_context_resolver: RouteRunResolver | None = None,
                 enable_run_header: bool = False) -> None:
        if type(enable_run_header) is not bool:
            raise ValueError("Explicit run-header configuration required")
        if enable_run_header and run_context_resolver is None:
            raise ValueError("Run headers require a trusted resolver")
        self.runtime = runtime
        self._resolver = run_context_resolver
        self._run_header = enable_run_header
        self.app = Starlette(routes=[Route("/{path:path}", self._dispatch, methods=list(_METHODS))])
        self.app.router.redirect_slashes = False

    def asgi_app(self) -> ManagedRestSurface:
        return self

    @property
    def supported_run_carriers(self) -> tuple[str, ...]:
        """SDK negotiation only; no new unauthenticated discovery endpoint."""
        return ("http-header:" + RUN_HEADER_NAME,) if self._run_header else ()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)

    async def _arguments(self, request: Request) -> dict[str, object]:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise _BadRequest(413, "body-too-large")
            body.extend(chunk)
        raw_query = request.scope.get("query_string", b"")
        if len(raw_query) > MAX_BODY_BYTES:
            raise _BadRequest(413, "query-too-large")
        if request.method in ("GET", "HEAD"):
            if body:
                raise _BadRequest(400, "read-body-unsupported")
            try:
                query = parse_qs(raw_query.decode("utf-8"), keep_blank_values=True,
                                 encoding="utf-8", errors="strict")
            except UnicodeError:
                raise _BadRequest(400, "invalid-query") from None
            return {key: values[0] if len(values) == 1 else values for key, values in query.items()}
        if raw_query:
            raise _BadRequest(400, "write-query-unsupported")
        media = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media and media != "application/json":
            raise _BadRequest(415, "unsupported-media-type")
        try:
            value = decode_json(bytes(body)) if body else {}
        except GraphError:
            raise _BadRequest(400, "invalid-json") from None
        if not isinstance(value, dict):
            raise _BadRequest(400, "invalid-body")
        return dict(value)

    async def _dispatch(self, request: Request) -> Response:
        head = request.method == "HEAD"
        try:
            try:
                supplied = validate_run_carrier(metadata={},
                    header_values=request.headers.getlist(RUN_HEADER_NAME),
                    alternate_header="x-contexture-run" in request.headers,
                    header_enabled=self._run_header, resolver_configured=self._resolver is not None)
            except RunCarrierError as failure:
                if failure.code == "unsupported_context":
                    raise _BadRequest(400, "run-header-unsupported") from None
                raise _BadRequest(413 if failure.code == "limit_exceeded" else 400,
                                  "invalid-run-context") from None
            scope: AbstractAsyncContextManager[RouteRequest]
            if supplied:
                if self._resolver is None:
                    raise GraphError("permission_denied")
                resolved = await self._resolver.resolve(request)
                if resolved is None:
                    raise GraphError("permission_denied")
                scope = resolved
            else:
                scope = self.runtime.request(publication_audience=True)
            async with scope as pinned:
                if supplied and getattr(pinned, "is_run_pinned", False) is not True:
                    raise GraphError("permission_denied")
                routes = await pinned.list_routes()
                matches = [p for p in routes if (p.method, p.path) ==
                           (request.method, request.scope["path"])]
                if not matches:
                    raise _BadRequest(404, "route-not-found")
                if len(matches) != 1:
                    raise _BadRequest(500, "invalid-route-contract")
                arguments = await self._arguments(request)
                result = await pinned.invoke_route(matches[0].publication_id, arguments,
                    expected_method=request.method, expected_path=request.scope["path"])
                body = _encoded(result)
                generation = pinned.generation_uuid
            return _response(body, 200, head=head, generation=generation)
        except _BadRequest as failure:
            status, code = failure.status, failure.code
        except GraphError as failure:
            status, code = ((400, "invalid-arguments") if failure.code == "schema_invalid"
                            else (403, "forbidden"))
        except PermissionError:
            status, code = 403, "forbidden"
        except Exception:
            # Do not log handler exceptions, graph diagnostics, locations or aliases.
            status, code = 500, "request-failed"
        return _response(json.dumps({"error": code}, separators=(",", ":")).encode(),
                         status, head=head)


__all__ = ["ManagedRestSurface", "RouteRequest", "RouteRuntime", "PinnedRouteRequest", "RouteRunResolver"]